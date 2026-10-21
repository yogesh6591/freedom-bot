"""Cal.com integration credentials (encrypted in DB, like Instantly / OpenAI)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from acquisition.services.encryption import CredentialEncryptionService, EncryptionError


class CalcomConfigError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CalcomSummary:
    api_key_configured: bool
    configured: bool
    source: str  # "database" | "environment" | "none"
    key_hint: str  # e.g. "…af8" or ""


@dataclass(frozen=True, slots=True)
class CalcomConnectionResult:
    connected: bool
    message: str
    category: str  # ok | auth_error | config_error | error
    http_status: int = 200


class CalcomService:
    def __init__(
        self,
        db: Session | None = None,
        settings: Settings | None = None,
        encryption: CredentialEncryptionService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._encryption = encryption or CredentialEncryptionService(self.settings)

    def get_summary(self) -> CalcomSummary:
        record = self._get_record()
        if record is not None:
            key = str(self._decrypt(record).get("api_key") or "").strip()
            if key:
                return CalcomSummary(
                    api_key_configured=True,
                    configured=True,
                    source="database",
                    key_hint=_key_hint(key),
                )

        env_key = self._env_key()
        if env_key:
            return CalcomSummary(
                api_key_configured=True,
                configured=True,
                source="environment",
                key_hint=_key_hint(env_key),
            )
        return CalcomSummary(
            api_key_configured=False,
            configured=False,
            source="none",
            key_hint="",
        )

    def save_api_key(self, api_key: str) -> CalcomSummary:
        self._require_db()
        cleaned = (api_key or "").strip()
        if not cleaned:
            raise CalcomConfigError("API key is required")
        if not cleaned.startswith("cal_"):
            raise CalcomConfigError("Cal.com API keys start with cal_")

        encrypted = self._encrypt(cleaned)
        record = self._get_record()
        if record is None:
            record = IntegrationConnection(
                provider=IntegrationProvider.CALCOM,
                display_name="Cal.com",
                encrypted_credentials=encrypted,
                connection_status=ConnectionStatus.CONNECTED,
            )
            self.db.add(record)
        else:
            record.encrypted_credentials = encrypted
            record.connection_status = ConnectionStatus.CONNECTED
            self.db.add(record)
        self.db.commit()
        return self.get_summary()

    def delete_connection(self) -> bool:
        """Remove stored Cal.com credentials. Returns True if a row was deleted."""
        self._require_db()
        record = self._get_record()
        if record is None:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

    def resolve_api_key(self) -> str | None:
        """Prefer encrypted DB credentials, then environment."""
        record = self._get_record()
        if record is not None:
            key = str(self._decrypt(record).get("api_key") or "").strip()
            if key:
                return key
        env_key = self._env_key()
        return env_key or None

    def check_connection(self) -> CalcomConnectionResult:
        """Call Cal.com /me to verify the API key."""
        from acquisition.integrations.calcom.client import CalcomClient, CalcomError

        key = self.resolve_api_key()
        if not key:
            return CalcomConnectionResult(
                connected=False,
                message="Cal.com API key is not configured",
                category="config_error",
            )
        try:
            CalcomClient(api_key=key, db=self.db).get_me()
        except CalcomError as exc:
            category = "auth_error" if exc.http_status in {401, 403} else "error"
            return CalcomConnectionResult(
                connected=False,
                message=exc.message,
                category=category,
                http_status=exc.http_status,
            )

        record = self._get_record()
        if record is not None and self.db is not None:
            record.connection_status = ConnectionStatus.CONNECTED
            self.db.add(record)
            self.db.commit()

        return CalcomConnectionResult(
            connected=True,
            message="",
            category="ok",
        )

    def _env_key(self) -> str:
        key = (getattr(self.settings, "calcom_api_key", None) or "").strip()
        if key:
            return key
        return (os.environ.get("CALCOM_API_KEY") or os.environ.get("CALENDLY_WEBHOOK_SECRET") or "").strip()

    def _get_record(self) -> IntegrationConnection | None:
        if self.db is None:
            return None
        return self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == IntegrationProvider.CALCOM
            )
        )

    def _encrypt(self, api_key: str) -> str:
        try:
            return self._encryption.encrypt_mapping({"api_key": api_key})
        except EncryptionError as exc:
            raise CalcomConfigError(exc.message) from exc

    def _decrypt(self, record: IntegrationConnection) -> dict:
        try:
            return self._encryption.decrypt_mapping(record.encrypted_credentials)
        except EncryptionError as exc:
            raise CalcomConfigError(exc.message) from exc

    def _require_db(self) -> None:
        if self.db is None:
            raise CalcomConfigError("Database session is required")


def _key_hint(api_key: str) -> str:
    cleaned = (api_key or "").strip()
    if len(cleaned) < 4:
        return ""
    return f"…{cleaned[-4:]}"
