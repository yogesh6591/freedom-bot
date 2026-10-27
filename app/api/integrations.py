"""Integration routes (§26 /api/integrations)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import bound, current_context, require_admin
from bizos.audit import events as audit
from bizos.connectors import composio
from bizos.connectors import registry as connectors
from bizos import settings
from bizos.connectors.base import ConnectorNotConfigured
from bizos.sync import store as sync_store
from bizos.tenancy.context import TenantContext
from bizos.types import AuditEventType

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class IntegrationUpdate(BaseModel):
    category: str
    provider: str
    display_name: str = ""
    enabled: bool = True
    status: str = "CONNECTED"
    #: Non-secret configuration only. Secret-shaped keys are stripped server-side.
    config: dict[str, Any] = Field(default_factory=dict)
    scopes: list[str] = Field(default_factory=list)


@router.get("")
def list_integrations(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Connection status per category, including not-yet-implemented ones."""
    with bound(ctx):
        return {
            "items": connectors.list_integrations(ctx),
            "connected": sorted(connectors.connected_categories(ctx)),
            "available_providers": {
                category: sorted(providers)
                for category, providers in connectors.PROVIDERS.items()
            },
        }


@router.post("")
def upsert(
    body: IntegrationUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    with bound(ctx):
        return connectors.set_integration(
            category=body.category,
            provider=body.provider,
            display_name=body.display_name,
            enabled=body.enabled,
            status=body.status,
            config=body.config,
            scopes=body.scopes,
            ctx=ctx,
        )


@router.post("/health")
def health(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Ping every connected integration."""
    with bound(ctx):
        return {"results": connectors.health_check(ctx)}


# ------------------------------------------------------------ composio (per user)


class ConnectRequest(BaseModel):
    #: Where Composio sends the browser after Google sign-in. Must be a UI origin.
    callback_url: Optional[str] = Field(default=None, max_length=2000)


def _safe_callback(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    from bizos import settings

    for origin in settings.cors_origins():
        if url == origin or url.startswith(origin.rstrip("/") + "/"):
            return url
    raise HTTPException(status_code=400, detail="callback_url must point at this app")


def _composio_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ConnectorNotConfigured):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=f"Composio request failed: {exc}")


@router.get("/composio")
def composio_status(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """The signed-in user's own Gmail / Google Calendar connections."""
    return composio.status(ctx)


@router.post("/composio/{toolkit}/connect")
def composio_connect(
    toolkit: str, body: ConnectRequest, ctx: TenantContext = Depends(current_context)
) -> dict[str, Any]:
    """Start Google sign-in for one toolkit; the UI redirects to ``redirect_url``."""
    callback = _safe_callback(body.callback_url)
    try:
        url = composio.connect_url(ctx, toolkit, callback)
    except Exception as exc:
        raise _composio_error(exc) from exc
    with bound(ctx):
        audit.log(AuditEventType.CONFIG_CHANGED, ctx=ctx, integration=toolkit, status="CONNECT_STARTED")
    return {"redirect_url": url}


@router.post("/composio/{toolkit}/disconnect")
def composio_disconnect(toolkit: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    try:
        composio.disconnect(ctx, toolkit)
    except Exception as exc:
        raise _composio_error(exc) from exc
    # Disconnecting also deletes what was synced from that source for this user.
    source = _SYNC_SOURCE.get(toolkit)
    if source:
        sync_store.forget(ctx, ctx.user_id, source)
    with bound(ctx):
        audit.log(AuditEventType.CONFIG_CHANGED, ctx=ctx, integration=toolkit, status="DISCONNECTED")
    return {"ok": True}


_SYNC_SOURCE = {"gmail": "gmail", "googlecalendar": "calendar", "youtube": "youtube", "hubspot": "hubspot"}


@router.get("/composio/sync")
def composio_sync_status(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """When each of the signed-in user's sources last synced, and how many records are kept."""
    from bizos.sync import scheduler

    return {"enabled": settings.integration_sync_enabled(), "interval_sec": settings.integration_sync_interval(),
            "next_run_in_sec": scheduler.seconds_until_next(), "sources": sync_store.status(ctx)}


@router.post("/composio/sync")
def composio_sync_now(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Sync the signed-in user's connected sources now."""
    from bizos.sync.runner import sync_user

    report = sync_user(ctx)
    return {"report": report, "sources": sync_store.status(ctx)}


@router.post("/composio/refresh")
def composio_refresh(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Drop the cached status, e.g. right after returning from Google sign-in."""
    composio.invalidate(ctx)
    return composio.status(ctx)
