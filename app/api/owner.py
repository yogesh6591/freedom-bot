"""FB-047 — JeanneCAIO exception-only owner view (not a CRM)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from bizos.control import store as control
from bizos.tenancy.context import TenantContext
import bizos.owner as owner

router = APIRouter(prefix="/api/owner", tags=["owner"])


class PortfolioBody(BaseModel):
    implementation_status: str = "live"
    account_risk: str = "healthy"
    owner_notes: str = Field(default="", max_length=2000)


@router.get("/portfolio")
def get_owner_portfolio(
    ctx: TenantContext = Depends(require_admin),
) -> dict[str, Any]:
    """Cross-client portfolio for Jeanne — metadata only, not a sales CRM."""
    _ = ctx
    return owner.owner_portfolio()


@router.put("/portfolio/{client_id}")
def put_portfolio(
    client_id: str,
    body: PortfolioBody,
    ctx: TenantContext = Depends(require_admin),
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
