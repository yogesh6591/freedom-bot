"""Human review queue routes (§26 /api/reviews)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import bound, current_context, require_drafter
from bizos.review import store as reviews
from bizos.tenancy.context import TenantContext
from bizos.types import ReviewStatus

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class Resolution(BaseModel):
    resolution: str = Field(min_length=1, max_length=2000)
    note: str = ""


class Dismissal(BaseModel):
    note: str = ""


@router.get("")
def list_reviews(
    status: Optional[str] = "OPEN",
    limit: int = Query(default=100, le=500),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    with bound(ctx):
        items = reviews.list_items(
            status=ReviewStatus.parse(status) if status else None,  # type: ignore[arg-type]
            limit=limit,
            ctx=ctx,
        )
        return {"items": [i.to_dict() for i in items], "open": reviews.open_count(ctx)}


@router.get("/{item_id}")
def get_review(item_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    with bound(ctx):
        try:
            return reviews.get(item_id, ctx=ctx).to_dict()
        except reviews.ReviewNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{item_id}/resolve")
def resolve(
    item_id: str, body: Resolution, ctx: TenantContext = Depends(require_drafter)
) -> dict[str, Any]:
    """Record the human's answer so the workflow can continue from it."""
    with bound(ctx):
        try:
            return reviews.resolve(item_id, resolution=body.resolution, note=body.note, ctx=ctx).to_dict()
        except reviews.ReviewNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{item_id}/dismiss")
def dismiss(
    item_id: str, body: Dismissal, ctx: TenantContext = Depends(require_drafter)
) -> dict[str, Any]:
    with bound(ctx):
        try:
            return reviews.dismiss(item_id, note=body.note, ctx=ctx).to_dict()
        except reviews.ReviewNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
