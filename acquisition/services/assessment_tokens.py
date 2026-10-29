"""Assessment token helpers and public personalized URL builder."""

from __future__ import annotations

import secrets
from urllib.parse import urljoin, urlparse

from acquisition.config import Settings, get_settings

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class PublicRedirectError(ValueError):
    """No URL the browser can open (Docker hostnames like api:8000 are refused)."""


def generate_assessment_token() -> str:
    return secrets.token_urlsafe(24)


def looks_like_browser_origin(url: str) -> bool:
    """True when a browser on the public internet (or local machine) can open this origin.

    Docker Compose service names such as ``api`` have no dot and are not loopback,
    so they must not be given to Stripe as a success/cancel URL.
    """
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if "." not in host and host not in _LOOPBACK_HOSTS:
        return False
    return True


def public_redirect_base(
    *,
    request_base: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Origin Stripe (and similar) should send the browser back to.

    Prefer PUBLIC_BASE_URL. ``request.base_url`` inside the API container is
    ``http://api:8000`` and must not win over the public site.
    """
    from acquisition.prefix_middleware import url_prefix

    cfg = settings or get_settings()
    configured = (cfg.public_base_url or "").strip().rstrip("/")
    incoming = (request_base or "").strip().rstrip("/")
    prefix = url_prefix()
    incoming_prefixed = incoming
    if incoming and prefix and not incoming.endswith(prefix):
        incoming_prefixed = incoming + prefix
    for candidate in (configured, incoming_prefixed, incoming):
        if candidate and looks_like_browser_origin(candidate):
            return candidate
    raise PublicRedirectError(
        "PUBLIC_BASE_URL must be a site the browser can open, not an internal host "
        "such as http://api:8000."
    )


def build_assessment_url(token: str, *, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    base = (cfg.public_base_url or "").rstrip("/") + "/"
    return urljoin(base, f"a/{token}")


def split_executive_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
