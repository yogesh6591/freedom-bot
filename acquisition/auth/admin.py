"""Admin authentication: session login, CSRF, route guards."""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import bcrypt
from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from acquisition.config import Settings, get_settings

logger = logging.getLogger(__name__)

SESSION_AUTH_KEY = "admin_authenticated"
SESSION_AT_KEY = "admin_authenticated_at"
SESSION_CSRF_KEY = "csrf_token"
SESSION_ROLE_KEY = "auth_role"

ROLE_ADMIN = "admin"
ROLE_ACQUISITION_OWNER = "acquisition_owner"

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AdminAuthError(Exception):
    """Raised when Admin auth configuration is unsafe for this environment."""


def assert_admin_auth_config(settings: Settings | None = None) -> None:
    """Fail closed in non-development when secrets are missing/weak."""
    cfg = settings or get_settings()
    env = (cfg.environment or "").strip().lower()
    has_env_creds = bool(
        (cfg.admin_password_hash or "").strip() and (cfg.admin_username or "").strip()
    )
    if env in {"development", "dev", "test"}:
        if not has_env_creds:
            logger.warning(
                "Admin credentials not set in env — use /login to create the first "
                "Admin username/password, or set ADMIN_USERNAME + ADMIN_PASSWORD_HASH"
            )
        return
    secret = (cfg.secret_key or "").strip()
    if not secret or secret == "change-me-in-production" or len(secret) < 32:
        raise AdminAuthError(
            "SECRET_KEY must be a strong non-default value in non-development environments"
        )
    # Production may bootstrap via DB create-account once; env credentials preferred.
    if not has_env_creds:
        logger.warning(
            "ADMIN_USERNAME/ADMIN_PASSWORD_HASH unset — ensure an Admin account "
            "exists in admin_auth_settings or create one at /login"
        )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.strip().encode("ascii"),
        )
    except (ValueError, TypeError):  # invalid hash format
        logger.warning("ADMIN_PASSWORD_HASH is not a valid bcrypt hash")
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def session_ttl(settings: Settings | None = None) -> timedelta:
    cfg = settings or get_settings()
    hours = max(1, int(cfg.admin_session_ttl_hours or 12))
    return timedelta(hours=hours)


def create_admin_session(request: Request, *, settings: Settings | None = None) -> None:
    """Mark the browser session as full Admin. Never store secrets."""
    _create_session(request, role=ROLE_ADMIN, settings=settings)


def create_acquisition_owner_session(
    request: Request, *, settings: Settings | None = None
) -> None:
    """Read-only acquisition-owner session (prospect DB view/export only)."""
    _create_session(request, role=ROLE_ACQUISITION_OWNER, settings=settings)


def _create_session(
    request: Request, *, role: str, settings: Settings | None = None
) -> None:
    now = _utcnow()
    request.session[SESSION_AUTH_KEY] = True
    request.session[SESSION_AT_KEY] = now.isoformat()
    request.session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)
    request.session[SESSION_ROLE_KEY] = role
    request.session["admin_expires_at"] = (now + session_ttl(settings)).isoformat()


def clear_admin_session(request: Request) -> None:
    request.session.clear()


def session_role(request: Request) -> str | None:
    if not _session_alive(request):
        return None
    role = request.session.get(SESSION_ROLE_KEY)
    if role in {ROLE_ADMIN, ROLE_ACQUISITION_OWNER}:
        return str(role)
    # Backward compatible: authenticated sessions without role are Admin.
    if request.session.get(SESSION_AUTH_KEY) is True:
        return ROLE_ADMIN
    return None


def _adopt_core_session(request: Request) -> bool:
    """FreedomBot workspace login is the only login. A signed admin session cookie
    opens acquisition with no second sign-in."""
    token = (request.cookies.get("freedombot_session") or "").strip()
    if not token:
        return False
    try:
        from freedombot.control.tokens import TokenError, roles_from_claims, verify_token
        from freedombot.types import Role

        payload = verify_token(token)
        roles = roles_from_claims(payload)
    except TokenError:
        return False
    except Exception:
        logger.debug("core session check skipped", exc_info=True)
        return False
    if Role.ADMIN not in roles:
        return False
    if request.session.get(SESSION_AUTH_KEY) is not True or request.session.get(SESSION_ROLE_KEY) != ROLE_ADMIN:
        _create_session(request, role=ROLE_ADMIN)
    return True


def _session_alive(request: Request, *, settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    if request.session.get(SESSION_AUTH_KEY) is not True:
        if _adopt_core_session(request):
            return True
        return False
    expires = _parse_iso(request.session.get("admin_expires_at"))
    issued = _parse_iso(request.session.get(SESSION_AT_KEY))
    now = _utcnow()
    if expires is not None and now > expires:
        clear_admin_session(request)
        return False
    if issued is not None and now > issued + session_ttl(cfg):
        clear_admin_session(request)
        return False
    return True


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def get_csrf_token(request: Request) -> str | None:
    token = request.session.get(SESSION_CSRF_KEY)
    return token if isinstance(token, str) and token else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def is_authenticated(request: Request, *, settings: Settings | None = None) -> bool:
    return _session_alive(request, settings=settings)


def is_admin_authenticated(request: Request, *, settings: Settings | None = None) -> bool:
    """True only for full Admin sessions (not acquisition-owner)."""
    if not _session_alive(request, settings=settings):
        return False
    return session_role(request) == ROLE_ADMIN


def is_acquisition_owner(request: Request, *, settings: Settings | None = None) -> bool:
    if not _session_alive(request, settings=settings):
        return False
    return session_role(request) == ROLE_ACQUISITION_OWNER


def can_view_acquisition_database(
    request: Request, *, settings: Settings | None = None
) -> bool:
    role = session_role(request) if _session_alive(request, settings=settings) else None
    return role in {ROLE_ADMIN, ROLE_ACQUISITION_OWNER}


def safe_next_url(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if not value.startswith("/") or value.startswith("//"):
        return None
    if value.startswith("/login") or value.startswith("/a/") or value.startswith("/api/webhooks/"):
        return None
    path = value.split("?", 1)[0].rstrip("/") or "/"
    # Never send the browser to POST-only Admin action URLs after login (GET → 405).
    post_only = {
        "/settings/ai/automation-config",
        "/settings/ai/icp",
        "/settings/ai/discovery-cycle",
        "/settings/ai/activation-config",
        "/settings/ai/activation-preview",
        "/settings/ai/activation-batch",
        "/settings/ai/live-e2e",
        "/settings/ai/learning-config",
        "/settings/ai/learning-recompute",
    }
    if path in post_only or path.startswith("/settings/ai/automation/"):
        return "/settings/ai"
    return value


def login_redirect_url(next_url: str | None = None) -> str:
    target = safe_next_url(next_url)
    if target:
        return f"/login?next={quote(target, safe='/:?=&%')}"
    return "/login"


async def _extract_csrf_from_request(request: Request) -> str | None:
    header = request.headers.get("x-csrf-token") or request.headers.get("X-CSRF-Token")
    if header and header.strip():
        return header.strip()
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        return None
    if (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        form = await request.form()
        raw = form.get("csrf_token")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


async def validate_csrf(request: Request) -> None:
    expected = get_csrf_token(request)
    provided = await _extract_csrf_from_request(request)
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )


def require_admin_html(request: Request) -> None:
    """Dependency for Admin HTML routes: redirect to login when unauthenticated."""
    if is_admin_authenticated(request):
        ensure_csrf_token(request)
        return
    # Owners hitting Admin-only pages go to their database home (or login).
    if is_acquisition_owner(request):
        raise _AdminLoginRedirect("/acquisition/database")
    path = request.url.path
    query = request.url.query
    nxt = path if not query else f"{path}?{query}"
    raise _AdminLoginRedirect(login_redirect_url(nxt))


async def require_admin_html_request(request: Request) -> None:
    """HTML guard including CSRF on mutating methods."""
    require_admin_html(request)
    if request.method.upper() in MUTATING_METHODS:
        await validate_csrf(request)


def require_acquisition_reader_html(request: Request) -> None:
    """Admin or acquisition-owner may view the permanent prospect database."""
    if can_view_acquisition_database(request):
        ensure_csrf_token(request)
        return
    path = request.url.path
    query = request.url.query
    nxt = path if not query else f"{path}?{query}"
    raise _AdminLoginRedirect(login_redirect_url(nxt))


async def require_acquisition_reader_html_request(request: Request) -> None:
    require_acquisition_reader_html(request)
    if request.method.upper() in MUTATING_METHODS:
        await validate_csrf(request)


class _AdminLoginRedirect(Exception):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(url)


def install_admin_exception_handlers(app: Any) -> None:
    @app.exception_handler(_AdminLoginRedirect)
    async def _handle_login_redirect(request: Request, exc: _AdminLoginRedirect):
        return RedirectResponse(url=exc.url, status_code=status.HTTP_303_SEE_OTHER)


async def require_admin_api(request: Request) -> None:
    """Dependency for Admin JSON APIs: 401 when unauthenticated; CSRF on mutations."""
    if not is_admin_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    ensure_csrf_token(request)
    if request.method.upper() in MUTATING_METHODS:
        await validate_csrf(request)
