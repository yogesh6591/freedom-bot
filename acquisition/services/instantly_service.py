"""Instantly admin configuration + connection checks (DB-backed credentials)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.instantly.client import InstantlyClient, InstantlyConnectionResult
from acquisition.integrations.instantly.errors import (
    InstantlyAuthError,
    InstantlyConfigError,
    InstantlyError,
    InstantlyProviderError,
    InstantlyTimeoutError,
)
from acquisition.integrations.instantly.redaction import redact_secrets
from acquisition.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from acquisition.schemas.integration import InstantlyConfigCreate, InstantlyConfigSummary, InstantlyConfigUpdate
from acquisition.services.encryption import CredentialEncryptionService, EncryptionError


class InstantlyConfigErrorApp(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InstantlyStatusResponse:
    connected: bool
    message: str
    category: str
    http_status: int
    display_name: str | None = None
    connection_status: ConnectionStatus | None = None
    api_key_configured: bool = False
    last_checked_at: datetime | None = None


class InstantlyService:
    def __init__(
        self,
        db: Session | None = None,
        settings: Settings | None = None,
        client: InstantlyClient | None = None,
        encryption: CredentialEncryptionService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._client = client
        self._encryption = encryption or CredentialEncryptionService(self.settings)

    def get_summary(self) -> InstantlyConfigSummary | None:
        record = self._get_record()
        if record is None:
            return None
        return self._to_summary(record)

    def resolve_api_key(self) -> str | None:
        """Decrypt stored Instantly API key for Admin Integrations UI."""
        record = self._get_record()
        if record is None:
            return None
        try:
            key = self._decrypt_api_key(record)
        except InstantlyConfigErrorApp:
            return None
        return key or None

    def create_connection(self, payload: InstantlyConfigCreate) -> InstantlyConfigSummary:
        self._require_db()
        if self._get_record() is not None:
            raise InstantlyConfigErrorApp("Instantly is already configured. Use replace to update the API key.")

        encrypted = self._encrypt_api_key(payload.api_key)
        record = IntegrationConnection(
            provider=IntegrationProvider.INSTANTLY,
            display_name=payload.display_name,
            encrypted_credentials=encrypted,
            connection_status=ConnectionStatus.UNKNOWN,
            last_checked_at=None,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_summary(record)

    def update_connection(self, payload: InstantlyConfigUpdate) -> InstantlyConfigSummary:
        self._require_db()
        record = self._get_record()
        if record is None:
            raise InstantlyConfigErrorApp("Instantly is not configured")

        data = payload.model_dump(exclude_unset=True)
        if "display_name" in data and data["display_name"]:
            record.display_name = data["display_name"]

        # Blank or missing api_key never erases the stored credential.
        if data.get("replace_api_key") and data.get("api_key"):
            record.encrypted_credentials = self._encrypt_api_key(data["api_key"])
            record.connection_status = ConnectionStatus.UNKNOWN
            record.last_checked_at = None

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return self._to_summary(record)

    def delete_connection(self) -> bool:
        """Remove stored Instantly credentials. Returns True if a row was deleted."""
        self._require_db()
        record = self._get_record()
        if record is None:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

    def check_connection(self) -> InstantlyStatusResponse:
        record = self._get_record() if self.db is not None else None
        summary_kwargs = {
            "display_name": record.display_name if record else None,
            "connection_status": record.connection_status if record else None,
            "api_key_configured": bool(record),
            "last_checked_at": record.last_checked_at if record else None,
        }

        try:
            client = self._resolve_client(record)
            result: InstantlyConnectionResult = client.check_connection()
            if record is not None and self.db is not None:
                record.connection_status = ConnectionStatus.CONNECTED
                record.last_checked_at = datetime.now(timezone.utc)
                self.db.add(record)
                self.db.commit()
                self.db.refresh(record)
                summary_kwargs.update(
                    {
                        "connection_status": record.connection_status,
                        "last_checked_at": record.last_checked_at,
                        "display_name": record.display_name,
                        "api_key_configured": True,
                    }
                )
            return InstantlyStatusResponse(
                connected=True,
                message=result.message,
                category="ok",
                http_status=200,
                **summary_kwargs,
            )
        except InstantlyConfigError as exc:
            return InstantlyStatusResponse(
                connected=False,
                message=self._safe_message(exc.message, default="Instantly is not configured"),
                category=exc.category,
                http_status=200,
                **summary_kwargs,
            )
        except InstantlyAuthError as exc:
            self._mark_error(record)
            if record is not None:
                summary_kwargs["connection_status"] = record.connection_status
                summary_kwargs["last_checked_at"] = record.last_checked_at
            return InstantlyStatusResponse(
                connected=False,
                message=self._safe_message(exc.message, default="Instantly authentication failed"),
                category=exc.category,
                http_status=200,
                **summary_kwargs,
            )
        except InstantlyTimeoutError as exc:
            self._mark_error(record)
            if record is not None:
                summary_kwargs["connection_status"] = record.connection_status
                summary_kwargs["last_checked_at"] = record.last_checked_at
            return InstantlyStatusResponse(
                connected=False,
                message=self._safe_message(exc.message, default="Instantly request timed out"),
                category=exc.category,
                http_status=503,
                **summary_kwargs,
            )
        except InstantlyProviderError as exc:
            self._mark_error(record)
            if record is not None:
                summary_kwargs["connection_status"] = record.connection_status
                summary_kwargs["last_checked_at"] = record.last_checked_at
            return InstantlyStatusResponse(
                connected=False,
                message=self._safe_message(exc.message, default="Instantly provider error"),
                category=exc.category,
                http_status=503,
                **summary_kwargs,
            )
        except EncryptionError as exc:
            return InstantlyStatusResponse(
                connected=False,
                message="Instantly credential encryption is not configured correctly",
                category="config_error",
                http_status=200,
                **summary_kwargs,
            )
        except InstantlyError as exc:
            self._mark_error(record)
            if record is not None:
                summary_kwargs["connection_status"] = record.connection_status
                summary_kwargs["last_checked_at"] = record.last_checked_at
            return InstantlyStatusResponse(
                connected=False,
                message=self._safe_message(exc.message, default="Instantly connection failed"),
                category=exc.category,
                http_status=503,
                **summary_kwargs,
            )

    def get_api_client(self) -> InstantlyClient:
        """Resolve InstantlyClient from encrypted DB credentials (or injected client)."""
        record = self._get_record()
        return self._resolve_client(record)

    def _resolve_client(self, record: IntegrationConnection | None) -> InstantlyClient:
        if self._client is not None:
            return self._client

        api_key = self._decrypt_api_key(record) if record is not None else ""
        if not api_key:
            raise InstantlyConfigError("Instantly API key is not configured")

        return InstantlyClient(
            api_key=api_key,
            base_url=self.settings.instantly_base_url,
            timeout_seconds=self.settings.instantly_timeout_seconds,
        )

    def _decrypt_api_key(self, record: IntegrationConnection) -> str:
        payload = self._encryption.decrypt_mapping(record.encrypted_credentials)
        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:
            raise InstantlyConfigError("Instantly API key is not configured")
        return api_key

    def _encrypt_api_key(self, api_key: str) -> str:
        cleaned = api_key.strip()
        if not cleaned:
            raise InstantlyConfigErrorApp("API key cannot be blank")
        try:
            return self._encryption.encrypt_mapping({"api_key": cleaned})
        except EncryptionError as exc:
            raise InstantlyConfigErrorApp(exc.message) from exc

    def _get_record(self) -> IntegrationConnection | None:
        if self.db is None:
            return None
        return self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == IntegrationProvider.INSTANTLY
            )
        )

    def _mark_error(self, record: IntegrationConnection | None) -> None:
        if record is None or self.db is None:
            return
        record.connection_status = ConnectionStatus.ERROR
        record.last_checked_at = datetime.now(timezone.utc)
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

    def _to_summary(self, record: IntegrationConnection) -> InstantlyConfigSummary:
        return InstantlyConfigSummary(
            id=record.id,
            provider=record.provider.value,
            display_name=record.display_name,
            api_key_configured=True,
            connection_status=record.connection_status,
            last_checked_at=record.last_checked_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _require_db(self) -> None:
        if self.db is None:
            raise InstantlyConfigErrorApp("Database session is required")

    def _safe_message(self, message: str, *, default: str) -> str:
        # Never include env leftovers; redact common patterns only.
        cleaned = redact_secrets(message, api_key=None).strip()
        return cleaned or default
