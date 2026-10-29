"""Stripe settings. Keys come from the server .env only; nothing is stored in the database."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings



@dataclass(frozen=True, slots=True)
class StripeSummary:
    secret_key_configured: bool
    webhook_secret_configured: bool
    configured: bool
    enabled: bool
    currency: str
    source: str  # "environment" | "none"
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
    def __init__(self, db: Session | None = None, settings: Settings | None = None) -> None:
        # ``db`` is accepted for the existing call sites; nothing is read from it any more.
        self.db = db
        self.settings = settings or get_settings()

    # -- values from the .env
    def resolve_secret_key(self) -> str | None:
        return (self.settings.stripe_secret_key or "").strip() or None

    def resolve_webhook_secret(self) -> str | None:
        return (getattr(self.settings, "stripe_webhook_secret", None) or "").strip() or None

    def resolve_currency(self) -> str:
        return (self.settings.stripe_currency or "usd").strip().lower() or "usd"

    def resolve_enabled(self) -> bool:
        return bool(self.settings.stripe_enabled)

    def get_summary(self) -> StripeSummary:
        key = self.resolve_secret_key()
        return StripeSummary(
            secret_key_configured=bool(key),
            webhook_secret_configured=bool(self.resolve_webhook_secret()),
            configured=bool(key),
            enabled=self.resolve_enabled() if key else False,
            currency=self.resolve_currency(),
            source="environment" if key else "none",
            key_hint=_key_hint(key or ""),
        )

    def check_connection(self) -> StripeConnectionResult:
        key = self.resolve_secret_key()
        if not key:
            return StripeConnectionResult(
                connected=False,
                message="STRIPE_SECRET_KEY is not set in the server .env",
                category="config_error",
            )
        try:
            import stripe
        except ImportError:
            return StripeConnectionResult(
                connected=False, message="stripe package is not installed", category="error"
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
        return StripeConnectionResult(connected=True, message="", category="ok")
