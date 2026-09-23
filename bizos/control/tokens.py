"""
Platform Tokens
===============

Issues and verifies the JWTs the UI and the API use.

The claims carry the caller's ``client_id`` and roles, so authorization never
needs a second lookup on the hot path and — more importantly — the client and
role are **signed**, not taken from a request body. A caller cannot elevate
themselves by editing a payload; they would have to forge an HMAC.

AgentOS is configured with the same secret, so a token minted here is also
accepted by agno's own routers, which then apply their ``user_isolation`` scoping
on the same ``sub``.
"""

from __future__ import annotations

from typing import Any, Optional

import jwt

from bizos import settings
from bizos.types import Role
from bizos.util.timeutil import in_seconds, utcnow

#: Claim names. Namespaced so they cannot collide with a registered claim.
CLAIM_CLIENT = "bizos_client"
CLAIM_ROLES = "bizos_roles"
CLAIM_EMAIL = "email"
CLAIM_NAME = "name"

_LEEWAY_SECONDS = 30


class TokenError(Exception):
    """A token was missing, malformed, expired or signed with the wrong key."""


def issue_token(
    *,
    user_id: str,
    client_id: str,
    roles: frozenset[Role] | set[Role],
    email: Optional[str] = None,
    display_name: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> tuple[str, int]:
    """Mint an access token. Returns ``(token, expires_in_seconds)``."""
    ttl = ttl_seconds or settings.jwt_ttl_seconds()
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(in_seconds(ttl).timestamp()),
        "iss": "bizos",
        CLAIM_CLIENT: client_id,
        CLAIM_ROLES: sorted(str(r) for r in roles),
    }
    if email:
        payload[CLAIM_EMAIL] = email
    if display_name:
        payload[CLAIM_NAME] = display_name
    token = jwt.encode(payload, settings.jwt_secret(), algorithm=settings.jwt_algorithm())
    return token, ttl


def verify_token(token: str) -> dict[str, Any]:
    """Verify signature and claims, returning the decoded payload.

    ``algorithms`` is pinned to the configured algorithm so a token cannot ask to
    be verified with ``none`` or with an asymmetric algorithm whose "public key"
    is our HMAC secret.
    """
    if not token:
        raise TokenError("No token supplied")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret(),
            algorithms=[settings.jwt_algorithm()],
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc
    if not payload.get("sub") or not payload.get(CLAIM_CLIENT):
        raise TokenError("Token is missing required claims")
    return payload


def roles_from_claims(payload: dict[str, Any]) -> frozenset[Role]:
    """Parse the signed role claim into the typed set, dropping unknown names."""
    raw = payload.get(CLAIM_ROLES) or []
    if isinstance(raw, str):
        raw = [raw]
    parsed = {Role.parse(item) for item in raw}
    return frozenset(r for r in parsed if r is not None)  # type: ignore[misc]
