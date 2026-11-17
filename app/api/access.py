"""
Access Requests API
===================

Anyone signed in can see their own access and ask for more. Only an admin can list or decide requests,
and the client id always comes from the caller's token.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import current_context, require_admin
from freedombot import admin_ops as ops
from freedombot.control import access_requests
from freedombot.tenancy.context import TenantContext

router = APIRouter(prefix="/api/access", tags=["access"])

T = TypeVar("T")


def _run(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except ops.AdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


class AccessRequestCreate(BaseModel):
    areas: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(default="", max_length=500)


class AccessRequestDecision(BaseModel):
    grant: bool


@router.get("")
def my_access(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    return ops.access_overview(ctx)


@router.post("/requests", status_code=201)
def create_request(
    body: AccessRequestCreate, ctx: TenantContext = Depends(current_context)
) -> dict[str, Any]:
    return _run(ops.request_access, ctx, body.areas, body.reason)


@router.get("/requests")
def list_requests(ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    return {"items": access_requests.pending(ctx.client_id)}


@router.post("/requests/{request_id}/decide")
def decide_request(
    request_id: str, body: AccessRequestDecision, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    return _run(ops.decide_access_request, ctx, request_id, grant=body.grant)
