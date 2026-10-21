"""OpenAI integration credentials (encrypted in DB, like Instantly)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from acquisition.services.encryption import CredentialEncryptionService, EncryptionError


class OpenAIConfigError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OpenAISummary:
    api_key_configured: bool
    model: str
    configured: bool
    source: str  # "database" | "environment" | "none"


@dataclass(frozen=True, slots=True)
class OpenAIConnectionResult:
    connected: bool
    message: str
    category: str  # ok | auth_error | config_error | error
    http_status: int = 200


class OpenAIService:
    def __init__(
        self,
        db: Session | None = None,
        settings: Settings | None = None,
        encryption: CredentialEncryptionService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._encryption = encryption or CredentialEncryptionService(self.settings)

    def get_summary(self) -> OpenAISummary:
        record = self._get_record()
        if record is not None:
            creds = self._decrypt(record)
            model = str(creds.get("model") or self.settings.openai_model or "gpt-4o-mini").strip()
            return OpenAISummary(
                api_key_configured=True,
                model=model or "gpt-4o-mini",
                configured=True,
                source="database",
            )

        env_key = bool((self.settings.openai_api_key or "").strip())
        env_provider = (self.settings.ai_reply_provider or "none").strip().lower()
        configured = env_provider == "openai" and env_key
        return OpenAISummary(
            api_key_configured=env_key,
            model=(self.settings.openai_model or "gpt-4o-mini").strip() or "gpt-4o-mini",
            configured=configured,
            source="environment" if configured else "none",
        )

    def save_api_key(self, api_key: str, *, model: str | None = None) -> OpenAISummary:
        self._require_db()
        cleaned = (api_key or "").strip()
        chosen_model = (model or self.settings.openai_model or "gpt-4o-mini").strip() or "gpt-4o-mini"
        record = self._get_record()

        # Blank key keeps the existing secret (production UI never re-sends it).
        if not cleaned:
            if record is None:
                raise OpenAIConfigError("API key is required")
            creds = self._decrypt(record)
            existing_key = str(creds.get("api_key") or "").strip()
            if not existing_key:
                raise OpenAIConfigError("API key is required")
            record.encrypted_credentials = self._encrypt(existing_key, chosen_model)
            record.connection_status = ConnectionStatus.CONNECTED
            self.db.add(record)
            self.db.commit()
            return self.get_summary()

        encrypted = self._encrypt(cleaned, chosen_model)
        if record is None:
            record = IntegrationConnection(
                provider=IntegrationProvider.OPENAI,
                display_name="OpenAI",
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
        """Remove stored OpenAI credentials. Returns True if a row was deleted."""
        self._require_db()
        record = self._get_record()
        if record is None:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

    def resolve_credentials(self) -> tuple[str | None, str]:
        """Return (api_key, model). Prefers encrypted DB credentials over env."""
        record = self._get_record()
        if record is not None:
            creds = self._decrypt(record)
            key = str(creds.get("api_key") or "").strip()
            model = str(creds.get("model") or self.settings.openai_model or "gpt-4o-mini").strip()
            if key:
                return key, model or "gpt-4o-mini"

        env_provider = (self.settings.ai_reply_provider or "none").strip().lower()
        env_key = (self.settings.openai_api_key or "").strip()
        if env_provider == "openai" and env_key:
            return env_key, (self.settings.openai_model or "gpt-4o-mini").strip() or "gpt-4o-mini"
        return None, (self.settings.openai_model or "gpt-4o-mini").strip() or "gpt-4o-mini"

    def check_connection(self) -> OpenAIConnectionResult:
        """Hit OpenAI models endpoint to verify the stored API key."""
        api_key, _model = self.resolve_credentials()
        if not api_key:
            # Also accept raw env key even if AI provider flag is off (Integrations test).
            api_key = (self.settings.openai_api_key or "").strip() or None
        if not api_key:
            return OpenAIConnectionResult(
                connected=False,
                message="OpenAI API key is not configured",
                category="config_error",
            )

        import httpx

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
        except httpx.HTTPError:
            return OpenAIConnectionResult(
                connected=False,
                message="Could not reach OpenAI",
                category="error",
                http_status=502,
            )

        if response.status_code in {401, 403}:
            return OpenAIConnectionResult(
                connected=False,
                message="OpenAI rejected the API key",
                category="auth_error",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            return OpenAIConnectionResult(
                connected=False,
                message="OpenAI connection failed",
                category="error",
                http_status=response.status_code,
            )

        record = self._get_record()
        if record is not None and self.db is not None:
            record.connection_status = ConnectionStatus.CONNECTED
            self.db.add(record)
            self.db.commit()

        return OpenAIConnectionResult(
            connected=True,
            message="",
            category="ok",
        )

    def _get_record(self) -> IntegrationConnection | None:
        if self.db is None:
            return None
        return self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == IntegrationProvider.OPENAI
            )
        )

    def _encrypt(self, api_key: str, model: str) -> str:
        try:
            return self._encryption.encrypt_mapping({"api_key": api_key, "model": model})
        except EncryptionError as exc:
            raise OpenAIConfigError(exc.message) from exc

    def _decrypt(self, record: IntegrationConnection) -> dict:
        try:
            return self._encryption.decrypt_mapping(record.encrypted_credentials)
        except EncryptionError as exc:
            raise OpenAIConfigError(exc.message) from exc

    def _require_db(self) -> None:
        if self.db is None:
            raise OpenAIConfigError("Database session is required")
