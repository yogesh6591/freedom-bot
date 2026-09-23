"""
Authentication Routes
=====================

Password login issuing a signed JWT, delivered two ways:

* an ``HttpOnly``, ``SameSite=Lax`` cookie for the browser UI — unreadable to page
  scripts, and ``SameSite=Lax`` blocks the cross-site POST that CSRF depends on;
* the raw token in the response body for API clients and tests.

Failed logins are audited to the control plane (they are not attributable to a
workspace until a user is identified), and the response never distinguishes
"no such account" from "wrong password".
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from app.api.deps import SESSION_COOKIE, current_context
from bizos import settings as app_settings
from bizos.control import store as control
from bizos.control.db import control_connection
from bizos.control.tokens import issue_token
from bizos.tenancy.context import TenantContext
from bizos.types import AuditEventType
from bizos.util.ids import new_id

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)
    #: Required only when the same email exists at more than one client.
    client_slug: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]
    client: dict[str, Any]


def _control_audit(event: AuditEventType, **fields: Any) -> None:
    """Audit to the control plane — used for events with no workspace yet."""
    try:
        with control_connection() as conn:
            conn.execute(
                text(
                    "INSERT INTO control_audit_events (id, event_type, client_id, user_id, "
                    "status, request, error) VALUES (:i, :e, :c, :u, :s, CAST(:r AS JSONB), :err)"
                ),
                {
                    "i": new_id("caud"),
                    "e": str(event),
                    "c": fields.get("client_id"),
                    "u": fields.get("user_id"),
                    "s": fields.get("status"),
                    "r": json.dumps(fields.get("request") or {}, default=str),
                    "err": fields.get("error"),
                },
            )
    except Exception:
        pass  # auditing must never break authentication


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Verify a password and issue a session token."""
    try:
        user = control.authenticate(body.email, body.password, client_slug=body.client_slug)
    except control.AuthenticationError as exc:
        _control_audit(
            AuditEventType.LOGIN_FAILED,
            status="FAILED",
            error=str(exc),
            # The submitted email is recorded so repeated attempts are visible;
            # the password never is.
            request={"email": body.email, "ip": request.client.host if request.client else None},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        ) from exc

    client = control.get_client(user.client_id)
    token, ttl = issue_token(
        user_id=user.id,
        client_id=user.client_id,
        roles=user.roles,
        email=user.email,
        display_name=user.display_name,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        secure=app_settings.cookie_secure(),
        samesite="lax",
        path="/",
    )
    _control_audit(
        AuditEventType.LOGIN,
        client_id=user.client_id,
        user_id=user.id,
        status="OK",
        request={"email": user.email},
    )
    # Also record it inside the workspace, where the customer's own audit view reads.
    try:
        from bizos.audit.events import log

        log(
            AuditEventType.LOGIN,
            ctx=control.build_context(user),
            status="OK",
            request={"email": user.email},
        )
    except Exception:
        pass
    return LoginResponse(
        access_token=token,
        expires_in=ttl,
        user=user.to_dict(),
        client={
            "id": client.id,
            "slug": client.slug,
            "company_name": client.company_name,
            "default_execution_mode": str(client.settings.mode),
            "enabled_domains": client.settings.enabled_domains,
            "allow_phi": client.settings.allow_phi,
            "allow_card_data": client.settings.allow_card_data,
        },
    )


@router.post("/logout")
def logout(response: Response, ctx: TenantContext = Depends(current_context)) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    _control_audit(AuditEventType.LOGOUT, client_id=ctx.client_id, user_id=ctx.user_id, status="OK")
    return {"status": "logged out"}


@router.get("/me")
def me(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Who the caller is, and what their workspace allows."""
    client = control.get_client(ctx.client_id)
    from bizos.domains.packs import PACKS_BY_NAME
    from bizos.rbac.permissions import permitted_tools

    return {
        "user": {
            "id": ctx.user_id,
            "email": ctx.email,
            "display_name": ctx.display_name,
            "roles": sorted(str(r) for r in ctx.roles),
            "primary_role": str(ctx.primary_role),
        },
        "client": {
            "id": client.id,
            "slug": client.slug,
            "company_name": client.company_name,
            "deployment_type": str(client.deployment_type),
            "default_execution_mode": str(client.settings.mode),
            "enabled_domains": client.settings.enabled_domains,
            "allow_phi": client.settings.allow_phi,
            "allow_card_data": client.settings.allow_card_data,
        },
        "domains": [
            PACKS_BY_NAME[d].to_dict() for d in client.settings.enabled_domains if d in PACKS_BY_NAME
        ],
        "permissions": [
            {
                "tool": p.tool,
                "permission": str(p.permission),
                "can_draft": p.can_draft,
                "can_invoke": p.can_invoke,
                "can_approve": p.can_approve,
            }
            for p in permitted_tools(ctx.roles, ctx=ctx)
        ],
    }
