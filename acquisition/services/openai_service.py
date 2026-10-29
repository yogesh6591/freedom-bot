"""OpenAI settings. The key and model come from the server .env only; nothing is stored in the database."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIConfigError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OpenAISummary:
    api_key_configured: bool
    model: str
    configured: bool  # a key is set AND AI_REPLY_PROVIDER=openai
    source: str  # "environment" | "none"


@dataclass(frozen=True, slots=True)
class OpenAIConnectionResult:
    connected: bool
    message: str
    category: str  # ok | auth_error | config_error | error
    http_status: int = 200


class OpenAIService:
    def __init__(self, db: Session | None = None, settings: Settings | None = None) -> None:
        # ``db`` is accepted for the existing call sites; nothing is read from it any more.
        self.db = db
        self.settings = settings or get_settings()

    def _key(self) -> str:
        return (self.settings.openai_api_key or "").strip()

    def _model(self) -> str:
        return (self.settings.openai_model or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    def get_summary(self) -> OpenAISummary:
        has_key = bool(self._key())
        provider_on = (self.settings.ai_reply_provider or "none").strip().lower() == "openai"
        configured = has_key and provider_on
        return OpenAISummary(
            api_key_configured=has_key,
            model=self._model(),
            configured=configured,
            source="environment" if configured else "none",
        )

    def resolve_credentials(self) -> tuple[str | None, str]:
        """(api_key, model) when AI_REPLY_PROVIDER=openai and a key is set; (None, model) otherwise."""
        if self.get_summary().configured:
            return self._key(), self._model()
        return None, self._model()

    def check_connection(self) -> OpenAIConnectionResult:
        """Ask OpenAI's models endpoint whether the .env key is accepted."""
        api_key = self._key()
        if not api_key:
            return OpenAIConnectionResult(
                connected=False,
                message="OPENAI_API_KEY is not set in the server .env",
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
                connected=False, message="Could not reach OpenAI", category="error", http_status=502
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
        return OpenAIConnectionResult(connected=True, message="", category="ok")
