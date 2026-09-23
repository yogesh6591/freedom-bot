"""Integration routes (§26 /api/integrations)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import bound, current_context, require_admin
from bizos.connectors import registry as connectors
from bizos.tenancy.context import TenantContext

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
