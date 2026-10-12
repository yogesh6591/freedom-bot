"""FB-047 — JeanneCAIO exception-only owner view (not a CRM).

Also hosts the checkout/contract sale webhook that starts assessment handoff
automatically (FB-043) so admins no longer paste JSON by hand.
"""

from __future__ import annotations

import hmac
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.api.deps import SESSION_COOKIE, require_platform_owner
from bizos import settings as app_settings
from bizos.audit import events as audit
from bizos.control import store as control
from bizos.control.tokens import TokenError, verify_token
from bizos.tenancy.context import TenantContext, tenant_scope
from bizos.types import AuditEventType
import bizos.owner as owner

router = APIRouter(prefix="/api/owner", tags=["owner"])

_bearer = HTTPBearer(auto_error=False)


class PortfolioBody(BaseModel):
    implementation_status: str = "live"
    account_risk: str = "healthy"
    owner_notes: str = Field(default="", max_length=2000)


class SaleEventBody(BaseModel):
    """Checkout or contract payload that starts First-client setup (FB-043)."""

    event: str = Field(
        description="checkout | contract | sale",
        min_length=3,
        max_length=40,
    )
    client_id: Optional[str] = None
    client_slug: Optional[str] = None
    assessment: dict[str, Any] = Field(default_factory=dict)


@router.get("/portfolio")
def get_owner_portfolio(
    ctx: TenantContext = Depends(require_platform_owner),
) -> dict[str, Any]:
    """Cross-client portfolio for Jeanne — metadata only, not a sales CRM.

    Platform owners only (M15-20): a client's own ADMIN must never see other
    clients' tickets, revenue or risk, or change them.
    """
    _ = ctx
    return owner.owner_portfolio()


@router.put("/portfolio/{client_id}")
def put_portfolio(
    client_id: str,
    body: PortfolioBody,
    ctx: TenantContext = Depends(require_platform_owner),
) -> dict[str, Any]:
    try:
        portfolio = owner.save_portfolio(
            client_id=client_id,
            implementation_status=body.implementation_status,
            account_risk=body.account_risk,
            owner_notes=body.owner_notes,
            ctx_user_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except control.ClientNotFound as exc:
        raise HTTPException(status_code=404, detail="Client not found") from exc
    return {"portfolio": portfolio}


def _resolve_client(*, client_id: Optional[str], client_slug: Optional[str]):
    if client_id:
        try:
            return control.get_client(client_id)
        except control.ClientNotFound as exc:
            raise HTTPException(status_code=404, detail="Client not found") from exc
    if client_slug:
        try:
            return control.get_client_by_slug(client_slug.strip().casefold())
        except control.ClientNotFound as exc:
            raise HTTPException(status_code=404, detail="Client not found") from exc
    raise HTTPException(status_code=400, detail="client_id or client_slug is required")


def _optional_platform_owner(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[TenantContext]:
    """Platform-owner context when a session is present; otherwise None."""
    token = ""
    if credentials and credentials.credentials:
        token = credentials.credentials
    else:
        token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    try:
        payload = verify_token(token)
    except TokenError:
        return None
    user_id = str(payload["sub"])
    try:
        user = control.get_user(user_id)
    except control.UserNotFound:
        return None
    if not user.platform_owner:
        return None
    return control.build_context(user, attributes={"platform_owner": True})


@router.post("/sale-events")
def sale_event_handoff(
    body: SaleEventBody,
    request: Request,
    owner_ctx: Optional[TenantContext] = Depends(_optional_platform_owner),
    x_freedombot_sale_secret: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Start assessment handoff automatically after checkout or contract (FB-043).

    Auth: platform-owner session **or** ``X-FreedomBot-Sale-Secret`` matching
    ``SALE_WEBHOOK_SECRET``. The assessment JSON from the sale system prefills
    First-client setup — no manual paste.
    """
    from bizos.insights import assessment_fingerprint, handoff_assessment

    secret = app_settings.sale_webhook_secret()
    header_secret = (x_freedombot_sale_secret or "").strip()
    authorized = owner_ctx is not None or bool(
        secret and header_secret and hmac.compare_digest(header_secret, secret)
    )
    if not authorized:
        raise HTTPException(status_code=401, detail="Sale webhook not authorized")

    event = body.event.strip().casefold()
    if event not in {"checkout", "contract", "sale"}:
        raise HTTPException(
            status_code=400,
            detail="event must be checkout, contract, or sale",
        )
    if not body.assessment:
        raise HTTPException(status_code=400, detail="assessment JSON object is required")

    client = _resolve_client(client_id=body.client_id, client_slug=body.client_slug)
    # FB-033: a deployment pinned to one client never takes another's sale.
    pinned = app_settings.pinned_client_slug()
    if pinned and client.slug != pinned:
        raise HTTPException(status_code=404, detail="Client not found")
    # Act inside the sold client's workspace (so its audit trail records the
    # handoff), attributed to the platform owner or to the webhook.
    handoff_ctx = control.system_context(
        client.id, user_id=owner_ctx.user_id if owner_ctx else "__sale_webhook__"
    )
    blob = client.settings.onboarding or {}
    # Checkout and contract often both fire for one sale: the same assessment
    # is handed off once, and the later event does not redo it.
    if blob.get("handoff_fingerprint") == assessment_fingerprint(body.assessment):
        return {
            "duplicate": True,
            "auto_started": False,
            "client_id": client.id,
            "client_slug": client.slug,
            "event": event,
            "handoff_at": blob.get("handoff_at"),
            "handoff_source": blob.get("handoff_source"),
            "message": "This assessment was already handed off; nothing changed.",
        }
    with tenant_scope(handoff_ctx):
        result = handoff_assessment(
            client_id=client.id,
            settings=client.settings,
            assessment=body.assessment,
            ctx=handoff_ctx,
            source=event,
            event=event,
        )
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=handoff_ctx,
            status="ASSESSMENT_HANDOFF_AUTO",
            request={"event": event, "via": "session" if owner_ctx else "webhook"},
            result={
                "open_questions": len(result["needs_clarification"]["open_questions"]),
                "inconsistencies": len(result["needs_clarification"]["inconsistencies"]),
            },
        )
    _ = request
    return {
        **result,
        "duplicate": False,
        "client_id": client.id,
        "client_slug": client.slug,
        "event": event,
        "auto_started": True,
    }
