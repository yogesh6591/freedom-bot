"""Integration routes (§26 /api/integrations)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, require_admin, run_scope
from bizos.audit import events as audit
from bizos.connectors import composio
from bizos.connectors import composio_catalog as catalog
from bizos.connectors import registry as connectors
from bizos import settings
from bizos.connectors.base import ConnectorNotConfigured
from bizos.control.models import ClientSettings
from bizos.sync import store as sync_store
from bizos.tenancy.context import TenantContext
from bizos.tools.base import guarded_call
from bizos.types import AuditEventType, PolicyEffect

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


class ToolChoice(BaseModel):
    #: True: the assistant may use every tool of this platform (the default).
    all: bool = True
    #: With all=False, the tool slugs it may use (from GET /composio/catalog/{slug}).
    actions: list[str] = Field(default_factory=list, max_length=2000)


def _connected(ctx: TenantContext) -> frozenset[str]:
    if not composio.configured():
        return frozenset()
    return frozenset(slug for slug, account in composio._active_accounts(ctx).items() if account)


@router.get("/composio/catalog")
def composio_catalog(
    q: str = Query(default="", max_length=100),
    category: str = Query(default="", max_length=60),
    connected_only: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=60, ge=1, le=120),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """Every platform Composio offers, searchable, with whether this user connected it and whether it can
    be connected here (Composio-managed sign-in, or set up in this Composio project)."""
    try:
        return catalog.search(q, category=category, connected=_connected(ctx), connected_only=connected_only,
                              offset=offset, limit=limit)
    except Exception as exc:
        raise _composio_error(exc) from exc


@router.get("/composio/catalog/{slug}")
def composio_platform(slug: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """One platform with its tools (and their tier) and which of them this user lets the assistant use."""
    try:
        slug = composio._check_toolkit(slug)
        item = catalog.platform(slug) or {"slug": slug, "name": composio.app_name(slug), "managed": True,
                                         "auth_schemes": [], "description": "", "logo": "", "categories": [],
                                         "tools_count": 0}
        tools = catalog.tools(slug)
    except Exception as exc:
        raise _composio_error(exc) from exc
    with bound(ctx):
        picked = catalog.chosen(ctx, slug)
    return {"platform": {**item, "connected": slug in _connected(ctx), "connectable": catalog.connectable(item)},
            "tools": tools, "all": picked is None, "selected": picked or []}


@router.put("/composio/catalog/{slug}/tools")
def composio_choose_tools(slug: str, body: ToolChoice, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Let the assistant use every tool of this platform, or only the chosen ones (for this user)."""
    try:
        slug = composio._check_toolkit(slug)
        known = {t["slug"] for t in catalog.tools(slug)}
    except Exception as exc:
        raise _composio_error(exc) from exc
    unknown = sorted(set(body.actions) - known)
    if not body.all and unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools for {slug}: {', '.join(unknown[:10])}")
    with bound(ctx):
        catalog.choose(ctx, slug, None if body.all else list(body.actions))
        audit.log(AuditEventType.CONFIG_CHANGED, ctx=ctx, integration=slug,
                  status="TOOLS_ALL" if body.all else f"TOOLS_SELECTED:{len(body.actions)}")
    return {"all": body.all, "selected": [] if body.all else sorted(set(body.actions))}


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


class CrmStorageChoice(BaseModel):
    destination: str = Field(..., max_length=40)


def _storage_outcome(ctx: TenantContext, settings_: ClientSettings, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a CRM storage tool through the same guard and policy check as the chat."""
    with bound(ctx):
        outcome = guarded_call(run_scope(ctx, settings_), tool, arguments)
    if outcome.effect == PolicyEffect.REQUIRE_APPROVAL and outcome.action_id:
        return {"pending_approval": True, "action_id": outcome.action_id, "message": outcome.message}
    if not outcome.ok:
        code = 403 if outcome.effect == PolicyEffect.DENY else 400
        raise HTTPException(status_code=code, detail=outcome.message or "That did not work.")
    return {**(outcome.data or {}), "message": outcome.message}


@router.get("/crm-storage")
def crm_storage_status(
    ctx: TenantContext = Depends(current_context),
    settings_: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Where the signed-in user's CRM records are saved (FreedomBot, HubSpot or Google Drive)."""
    return _storage_outcome(ctx, settings_, "crm_get_storage", {})


@router.put("/crm-storage")
def crm_storage_choose(
    body: CrmStorageChoice,
    ctx: TenantContext = Depends(current_context),
    settings_: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Choose where the signed-in user's new CRM records are saved (the chat's crm_set_storage tool)."""
    return _storage_outcome(ctx, settings_, "crm_set_storage", {"destination": body.destination})


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
