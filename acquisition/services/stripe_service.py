"""Stripe integration credentials (encrypted in DB, env fallback like Cal.com)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.integration import ConnectionStatus, IntegrationConnection, IntegrationProvider
from acquisition.services.encryption import CredentialEncryptionService, EncryptionError


class StripeConfigError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class StripeSummary:
    secret_key_configured: bool
    webhook_secret_configured: bool
    configured: bool
    enabled: bool
    currency: str
    source: str  # "database" | "environment" | "none"
    key_hint: str


@dataclass(frozen=True, slots=True)
class StripeConnectionResult:
    connected: bool
    message: str
    category: str  # ok | auth_error | config_error | error
    http_status: int = 200


def _key_hint(key: str) -> str:
    cleaned = (key or "").strip()
    if len(cleaned) < 8:
        return ""
    return f"…{cleaned[-4:]}"


class StripeService:
    def __init__(
        self,
        db: Session | None = None,
        settings: Settings | None = None,
        encryption: CredentialEncryptionService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._encryption = encryption or CredentialEncryptionService(self.settings)

    def get_summary(self) -> StripeSummary:
        record = self._get_record()
        if record is not None:
            creds = self._decrypt(record)
            key = str(creds.get("secret_key") or "").strip()
            if key:
                wh = str(creds.get("webhook_secret") or "").strip()
                currency = str(creds.get("currency") or self.settings.stripe_currency or "usd").strip() or "usd"
                enabled = bool(creds.get("enabled", True))
                return StripeSummary(
                    secret_key_configured=True,
                    webhook_secret_configured=bool(wh),
                    configured=True,
                    enabled=enabled,
                    currency=currency.lower(),
                    source="database",
                    key_hint=_key_hint(key),
                )

        env_key = self._env_secret_key()
        if env_key:
            return StripeSummary(
                secret_key_configured=True,
                webhook_secret_configured=bool(self._env_webhook_secret()),
                configured=True,
                enabled=bool(self.settings.stripe_enabled),
                currency=(self.settings.stripe_currency or "usd").strip().lower() or "usd",
                source="environment",
                key_hint=_key_hint(env_key),
            )
        return StripeSummary(
            secret_key_configured=False,
            webhook_secret_configured=False,
            configured=False,
            enabled=False,
            currency=(self.settings.stripe_currency or "usd").strip().lower() or "usd",
            source="none",
            key_hint="",
        )

    def save_credentials(
        self,
        *,
        secret_key: str = "",
        webhook_secret: str = "",
        currency: str = "usd",
        enabled: bool = True,
    ) -> StripeSummary:
        self._require_db()
        cleaned_key = (secret_key or "").strip()
        cleaned_wh = (webhook_secret or "").strip()
        chosen_currency = (currency or "usd").strip().lower() or "usd"
        record = self._get_record()

        if not cleaned_key:
            if record is None:
                raise StripeConfigError("Stripe secret key is required")
            existing = self._decrypt(record)
            cleaned_key = str(existing.get("secret_key") or "").strip()
            if not cleaned_key:
                raise StripeConfigError("Stripe secret key is required")
            if not cleaned_wh:
                cleaned_wh = str(existing.get("webhook_secret") or "").strip()

        if not (cleaned_key.startswith("sk_test_") or cleaned_key.startswith("sk_live_")):
            raise StripeConfigError("Stripe secret keys start with sk_test_ or sk_live_")
        if cleaned_wh and not cleaned_wh.startswith("whsec_"):
            raise StripeConfigError("Stripe webhook secrets start with whsec_")

        encrypted = self._encrypt(
            secret_key=cleaned_key,
            webhook_secret=cleaned_wh,
            currency=chosen_currency,
            enabled=enabled,
        )
        if record is None:
            record = IntegrationConnection(
                provider=IntegrationProvider.STRIPE,
                display_name="Stripe",
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
        self._require_db()
        record = self._get_record()
        if record is None:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

    def resolve_secret_key(self) -> str | None:
        """Prefer encrypted DB credentials, then environment."""
        record = self._get_record()
        if record is not None:
            key = str(self._decrypt(record).get("secret_key") or "").strip()
            if key:
                return key
        return self._env_secret_key() or None

    def resolve_webhook_secret(self) -> str | None:
        record = self._get_record()
        if record is not None:
            wh = str(self._decrypt(record).get("webhook_secret") or "").strip()
            if wh:
                return wh
        return self._env_webhook_secret() or None

    def resolve_currency(self) -> str:
        record = self._get_record()
        if record is not None:
            currency = str(self._decrypt(record).get("currency") or "").strip().lower()
            if currency:
                return currency
        return (self.settings.stripe_currency or "usd").strip().lower() or "usd"

    def resolve_enabled(self) -> bool:
        record = self._get_record()
        if record is not None:
            creds = self._decrypt(record)
            if str(creds.get("secret_key") or "").strip():
                return bool(creds.get("enabled", True))
        return bool(self.settings.stripe_enabled)

    def check_connection(self) -> StripeConnectionResult:
        key = self.resolve_secret_key()
        if not key:
            return StripeConnectionResult(
                connected=False,
                message="Stripe secret key is not configured",
                category="config_error",
            )
        try:
            import stripe
        except ImportError:
            return StripeConnectionResult(
                connected=False,
                message="stripe package is not installed",
                category="error",
            )
        try:
            stripe.api_key = key
            stripe.Balance.retrieve()
        except Exception as exc:  # noqa: BLE001
            msg = str(getattr(exc, "user_message", None) or exc)[:240]
            auth = "auth" in msg.lower() or "invalid api key" in msg.lower()
            return StripeConnectionResult(
                connected=False,
                message=msg or "Stripe connection failed",
                category="auth_error" if auth else "error",
                http_status=401 if auth else 200,
            )

        record = self._get_record()
        if record is not None and self.db is not None:
            record.connection_status = ConnectionStatus.CONNECTED
            self.db.add(record)
            self.db.commit()

        return StripeConnectionResult(connected=True, message="", category="ok")

    def _env_secret_key(self) -> str:
        return (self.settings.stripe_secret_key or "").strip()

    def _env_webhook_secret(self) -> str:
        return (getattr(self.settings, "stripe_webhook_secret", None) or "").strip()

    def _get_record(self) -> IntegrationConnection | None:
        if self.db is None:
            return None
        return self.db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.provider == IntegrationProvider.STRIPE
            )
        )

    def _encrypt(
        self,
        *,
        secret_key: str,
        webhook_secret: str,
        currency: str,
        enabled: bool,
    ) -> str:
        try:
            return self._encryption.encrypt_mapping(
                {
                    "secret_key": secret_key,
                    "webhook_secret": webhook_secret,
                    "currency": currency,
                    "enabled": enabled,
                }
            )
        except EncryptionError as exc:
            raise StripeConfigError(exc.message) from exc

    def _decrypt(self, record: IntegrationConnection) -> dict:
        try:
            return self._encryption.decrypt_mapping(record.encrypted_credentials)
        except EncryptionError as exc:
            raise StripeConfigError(exc.message) from exc

    def _require_db(self) -> None:
        if self.db is None:
            raise StripeConfigError("Database session is required")
