"""
API Dependencies
================

Turns an HTTP request into a verified :class:`~bizos.tenancy.context.TenantContext`.

This is the platform's authentication boundary, and it has one rule: **the client
and the roles come from the signed token, never from the request**. There is no
``client_id`` query parameter, no ``X-Tenant`` header and no role field in any
request body. A caller who wants a different workspace or a higher role has to
forge an HMAC, not edit JSON.

The resolved context is bound with :func:`bizos.tenancy.tenant_scope` for the
duration of the request, so every store the handler touches reaches exactly one
workspace, and the binding is released even if the handler raises.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from bizos.control import store as control
from bizos.control.tokens import TokenError, roles_from_claims, verify_token
from bizos.control.models import ClientSettings
from bizos.tenancy.context import TenantContext, tenant_scope
from bizos.tools.base import RunScope
from bizos.types import ExecutionMode, Role

#: Cookie the browser UI uses. ``HttpOnly`` so page scripts cannot read it, which
#: is what keeps an XSS from becoming a token theft.
SESSION_COOKIE = "bizos_session"

_bearer = HTTPBearer(auto_error=False)


def _token_from(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> str:
    """Bearer header first (API clients), then the session cookie (the UI)."""
    if credentials and credentials.credentials:
        return credentials.credentials
    return request.cookies.get(SESSION_COOKIE, "")


def current_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> TenantContext:
    """The verified tenant context for this request, or 401."""
    token = _token_from(request, credentials)
    try:
        payload = verify_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = str(payload["sub"])
    client_id = str(payload["bizos_client"])
    try:
        client = control.get_client(client_id)
    except control.ClientNotFound as exc:
        raise HTTPException(status_code=401, detail="Unknown workspace") from exc
    if client.status != "ACTIVE":
        raise HTTPException(status_code=403, detail="Workspace is not active")

    # The roles in the token are signed, but the user record is authoritative for
    # revocation: a role removed a minute ago must not stay usable until the
    # token expires. The intersection is what the caller actually gets.
    try:
        user = control.get_user(user_id)
    except control.UserNotFound as exc:
        raise HTTPException(status_code=401, detail="Unknown user") from exc
    if user.client_id != client_id:
        # A token whose subject belongs to another workspace. Refuse loudly.
        raise HTTPException(status_code=403, detail="Token does not match this workspace")
    if user.status != "ACTIVE":
        raise HTTPException(status_code=403, detail="Account is not active")

    roles = frozenset(roles_from_claims(payload)) & user.roles

    return TenantContext(
        client_id=client.id,
        client_slug=client.slug,
        user_id=user.id,
        roles=roles or frozenset({Role.VIEWER}),
        deployment_type=client.deployment_type,
        db_name=client.db_name,
        db_schema=client.db_schema,
        default_execution_mode=client.settings.mode,
        email=user.email,
        display_name=user.display_name,
        attributes={"ip": request.client.host if request.client else None},
    )


def client_settings(ctx: TenantContext = Depends(current_context)) -> ClientSettings:
    """The requesting client's configuration."""
    return control.get_client(ctx.client_id).settings


def require_role(*roles: Role):
    """Dependency factory: require at least one of ``roles`` (ADMIN always passes)."""

    def _check(ctx: TenantContext = Depends(current_context)) -> TenantContext:
        if ctx.is_admin or any(ctx.has_role(r) for r in roles):
            return ctx
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This action requires one of: {', '.join(str(r) for r in roles)}. "
                f"You hold: {', '.join(sorted(str(r) for r in ctx.roles))}."
            ),
        )

    return _check


require_admin = require_role(Role.ADMIN)
require_approver = require_role(Role.APPROVER)
require_operator = require_role(Role.OPERATOR)
require_drafter = require_role(Role.DRAFTER, Role.APPROVER, Role.OPERATOR)


@contextmanager
def bound(ctx: TenantContext) -> Iterator[TenantContext]:
    """Bind the tenant for the body of a handler."""
    with tenant_scope(ctx) as bound_ctx:
        yield bound_ctx


def run_scope(
    ctx: TenantContext,
    settings: ClientSettings,
    *,
    domain: str = "general",
    requested_mode: Optional[str] = None,
    session_id: Optional[str] = None,
) -> RunScope:
    """Build a RunScope from verified request context."""
    mode = ExecutionMode.parse(requested_mode) if requested_mode else None
    return RunScope(
        ctx=ctx,
        settings=settings,
        domain=domain,
        requested_mode=mode,  # type: ignore[arg-type]
        session_id=session_id,
    )
