"""
Platform Settings
=================

All environment-driven configuration in one place. Nothing else in the platform
calls :func:`os.getenv` for configuration, so ``.env.example`` stays the single
source of truth for what is configurable.
"""

from __future__ import annotations

from functools import lru_cache
from os import getenv
from urllib.parse import quote

from bizos.types import DeploymentType, ExecutionMode


def runtime_env() -> str:
    """``RUNTIME_ENV`` — ``dev`` or ``prd``. Defaults to ``prd`` (fail secure)."""
    return getenv("RUNTIME_ENV", "prd").strip().lower()


def is_prd() -> bool:
    """True unless explicitly running in dev. Auth strictness keys off this."""
    return runtime_env() != "dev"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def db_driver() -> str:
    return getenv("DB_DRIVER", "postgresql+psycopg")


def _db_parts() -> tuple[str, str, str, str]:
    return (
        getenv("DB_USER", "bizos"),
        quote(getenv("DB_PASS", "bizos"), safe=""),
        getenv("DB_HOST", "localhost"),
        getenv("DB_PORT", "5432"),
    )


def control_database() -> str:
    """Name of the control-plane database (clients, users, roles)."""
    return getenv("CONTROL_DB_NAME", "bizos_control")


def database_url(database: str) -> str:
    """Build a SQLAlchemy URL for ``database`` on the configured server."""
    user, password, host, port = _db_parts()
    return f"{db_driver()}://{user}:{password}@{host}:{port}/{database}"


def control_db_url() -> str:
    """SQLAlchemy URL for the control plane."""
    override = getenv("CONTROL_DB_URL", "").strip()
    return override or database_url(control_database())


def workspace_db_prefix() -> str:
    """Prefix for provisioned per-client databases (``DEDICATED_DB`` deployments)."""
    return getenv("WORKSPACE_DB_PREFIX", "bizos_ws_")


def shared_workspace_database() -> str:
    """Database that holds per-client *schemas* in ``DEDICATED_SCHEMA`` deployments."""
    return getenv("SHARED_WORKSPACE_DB", "bizos_workspaces")


def default_deployment_type() -> DeploymentType:
    """Isolation model new clients get unless overridden at creation time."""
    return DeploymentType.parse(getenv("DEFAULT_DEPLOYMENT_TYPE", ""), DeploymentType.DEDICATED_DB)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def jwt_secret() -> str:
    """HMAC key for platform-issued JWTs.

    There is deliberately no usable default in production: a shipped default
    signing key means anyone can mint an ADMIN token. In dev a fixed development
    key is used so the stack boots without configuration.
    """
    secret = getenv("JWT_SECRET", "").strip()
    if secret:
        # HS256 keys shorter than the hash output weaken the MAC (RFC 7518 §3.2).
        if len(secret.encode("utf-8")) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 bytes.")
        return secret
    if is_prd():
        raise RuntimeError(
            "JWT_SECRET is not set. Refusing to sign tokens with a default key in production."
        )
    return "dev-only-insecure-signing-key-not-for-production-use"


def jwt_algorithm() -> str:
    return getenv("JWT_ALGORITHM", "HS256")


def jwt_ttl_seconds() -> int:
    """Access-token lifetime. Short by default; the UI re-authenticates."""
    try:
        return int(getenv("JWT_TTL_SECONDS", "43200"))
    except ValueError:
        return 43200


def cookie_secure() -> bool:
    """Whether the session cookie carries ``Secure``. Off only in dev over http."""
    raw = getenv("COOKIE_SECURE", "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return is_prd()


def cors_origins() -> list[str]:
    """Browser origins allowed to call the API (the UI's origin)."""
    raw = getenv("CORS_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


# ---------------------------------------------------------------------------
# Platform defaults
# ---------------------------------------------------------------------------


def default_execution_mode() -> ExecutionMode:
    """Execution mode a newly created client starts in.

    Defaults to ``WAIT_FOR_APPROVAL`` — a new workspace should never begin life
    able to write to a customer's real systems unattended.
    """
    return ExecutionMode.parse(  # type: ignore[return-value]
        getenv("DEFAULT_EXECUTION_MODE", ""), ExecutionMode.WAIT_FOR_APPROVAL
    )


def agentos_url() -> str:
    """Public origin of this AgentOS (scheduler callbacks, MCP OAuth issuer)."""
    return getenv("AGENTOS_URL", "http://127.0.0.1:8000")


def internal_service_token() -> str | None:
    return getenv("INTERNAL_SERVICE_TOKEN", "").strip() or None


def sale_webhook_secret() -> str | None:
    """Shared secret for checkout/contract → assessment handoff (FB-043).

    Checkout or contract systems POST to ``/api/owner/sale-events`` with header
    ``X-FreedomBot-Sale-Secret``. When unset, only a signed-in platform owner
    may trigger automatic handoff.
    """
    return getenv("SALE_WEBHOOK_SECRET", "").strip() or None


def openai_api_key() -> str | None:
    return getenv("OPENAI_API_KEY", "").strip() or None


def model_id() -> str:
    """Chat model for Agno. Must be ``provider:model`` (e.g. ``openai:gpt-4.1-mini``)."""
    raw = getenv("MODEL_ID", "openai:gpt-4.1-mini").strip()
    if raw and ":" not in raw:
        # Older env files used bare ids; Agno 3 requires a provider prefix.
        return f"openai:{raw}"
    return raw or "openai:gpt-4.1-mini"


def llm_vendor() -> str:
    """Configured chat model vendor (FB-039 BAA pattern). Defaults from MODEL_ID."""
    override = getenv("LLM_VENDOR", "").strip().casefold()
    if override:
        return override
    mid = model_id()
    return mid.split(":", 1)[0].casefold() if ":" in mid else "openai"


@lru_cache(maxsize=1)
def llm_available() -> bool:
    """Whether an LLM is configured. Non-LLM features must work without one."""
    return openai_api_key() is not None


def memory_vector_search() -> bool:
    """Meaning-based memory search (bizos/memory/vectors.py). On unless MEMORY_VECTOR_SEARCH is off."""
    return getenv("MEMORY_VECTOR_SEARCH", "on").strip().casefold() not in ("0", "off", "false", "no")


def integration_sync_enabled() -> bool:
    """Background sync of each user's Composio sources (bizos/sync). On unless INTEGRATION_SYNC_ENABLED is off."""
    return getenv("INTEGRATION_SYNC_ENABLED", "on").strip().casefold() not in ("0", "off", "false", "no")


def integration_sync_interval() -> int:
    """Seconds between background syncs (minimum 60, default 300)."""
    try:
        return max(60, int(getenv("INTEGRATION_SYNC_INTERVAL_SEC", "300")))
    except ValueError:
        return 300


def executor_reasoning_effort() -> str | None:
    """Reasoning effort for the v2 executor (low|medium|high), for reasoning models only; unset = off."""
    value = getenv("EXECUTOR_REASONING_EFFORT", "").strip().casefold()
    return value if value in ("minimal", "low", "medium", "high") else None


def web_search_provider() -> str:
    """web_search backend: openai (default, OpenAI's built-in web search) or tavily."""
    return "tavily" if getenv("WEB_SEARCH_PROVIDER", "").strip().casefold() == "tavily" else "openai"


def web_search_model_id() -> str:
    """OpenAI model that runs web searches (needs the Responses API web_search tool)."""
    return getenv("WEB_SEARCH_MODEL_ID", "").strip().removeprefix("openai:") or "gpt-5.5"


def web_search_reasoning_effort() -> str | None:
    """Reasoning effort for the search model (default low: about 20 s; high is slower). "none" turns it off."""
    value = getenv("WEB_SEARCH_REASONING_EFFORT", "low").strip().casefold()
    return value if value in ("minimal", "low", "medium", "high") else None


def transcript_api_key() -> str:
    """Supadata key for YouTube transcripts (TRANSCRIPT_API_KEY, or SUPADATA_API_KEY)."""
    return (getenv("TRANSCRIPT_API_KEY", "") or getenv("SUPADATA_API_KEY", "")).strip()


def gemini_api_key() -> str:
    """Google Gemini key: YouTube transcripts of public videos when YouTube blocks this server."""
    return getenv("GEMINI_API_KEY", "").strip()


def gemini_model_id() -> str:
    """Gemini model that transcribes YouTube videos."""
    return getenv("GEMINI_MODEL_ID", "").strip().removeprefix("models/") or "gemini-3.8-flash"


def tavily_api_key() -> str:
    """Tavily key, used only when WEB_SEARCH_PROVIDER=tavily."""
    return getenv("TAVILY_API_KEY", "").strip()


def embedding_model_id() -> str:
    """OpenAI embedding model for memory search (1536 dimensions)."""
    return getenv("EMBEDDING_MODEL_ID", "").strip() or "text-embedding-3-small"


def pinned_client_slug() -> str | None:
    """FB-033: the one client this deployment serves, or ``None`` for shared dev.

    Set per client by ``scripts/provision_client.py`` in that client's own
    deployment. When set, the API refuses sign-ins and tokens for any other
    client, so one FreedomBot deployment is walled to one company.
    """
    raw = getenv("CLIENT_SLUG", "").strip().casefold()
    return raw or None
