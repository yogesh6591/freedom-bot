"""Organizational memory routes (§26 /api/memory)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, require_drafter
from bizos.control.models import ClientSettings
from bizos.memory import store as memory
from bizos.tenancy.context import TenantContext
from bizos.types import ApprovalStatus, MemoryCategory, Role, SourceType

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryWrite(BaseModel):
    category: str = Field(default="FACT")
    title: str
    content: str
    key: Optional[str] = None
    domain: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "MANUAL"
    source_id: Optional[str] = None
    confidence: float = 1.0
    #: FB-037 restricted area (exec/hr/salary/finance/legal), or None.
    access_area: Optional[str] = None
    #: FB-036 policy topic; two differing values on one topic are a conflict.
    topic: Optional[str] = None


class MemoryCorrection(BaseModel):
    new_content: str
    reason: str = Field(min_length=1)
    source_type: str = "MANUAL"
    confidence: float = 1.0


@router.get("")
def list_memory(
    category: Optional[str] = None,
    domain: Optional[str] = None,
    include_unapproved: bool = True,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """List memory items with their current version."""
    with bound(ctx):
        items = memory.list_items(
            category=MemoryCategory.parse(category) if category else None,  # type: ignore[arg-type]
            domain=domain,
            include_unapproved=include_unapproved,
            limit=limit,
            offset=offset,
            ctx=ctx,
        )
        return {"items": [i.to_dict() for i in items], "count": len(items)}


@router.get("/search")
def search_memory(
    q: str = "",
    authoritative_only: bool = True,
    limit: int = Query(default=10, le=100),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    with bound(ctx):
        items = memory.search(q, authoritative_only=authoritative_only, limit=limit, ctx=ctx)
        return {"items": [i.to_dict() for i in items]}


@router.get("/conflicts")
def list_conflicts(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Topics where recorded policies disagree and a person must pick one."""
    with bound(ctx):
        items = memory.find_conflicts(ctx=ctx)
        return {"items": items, "count": len(items)}


@router.get("/{item_id}")
def get_memory(item_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """One item with its full version history, including superseded values."""
    with bound(ctx):
        try:
            return memory.history(item_id, ctx=ctx).to_dict(include_versions=True)
        except memory.MemoryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=201)
def create_memory(
    body: MemoryWrite,
    ctx: TenantContext = Depends(require_drafter),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    attributes = dict(body.attributes)
    if body.topic:
        attributes["topic"] = body.topic
    with bound(ctx):
        try:
            item = memory.put(
                category=MemoryCategory.parse(body.category, MemoryCategory.FACT),  # type: ignore[arg-type]
                title=body.title,
                content=body.content,
                memory_key=body.key,
                domain=body.domain,
                tags=body.tags,
                attributes=attributes,
                access_area=body.access_area,
                source_type=SourceType.parse(body.source_type, SourceType.MANUAL),  # type: ignore[arg-type]
                source_id=body.source_id,
                confidence=body.confidence,
                settings=settings,
                ctx=ctx,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return item.to_dict()


@router.post("/{item_id}/correct")
def correct_memory(
    item_id: str,
    body: MemoryCorrection,
    ctx: TenantContext = Depends(require_drafter),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Supersede a value. The previous version is preserved, never overwritten."""
    required = Role.parse(settings.memory_policy.correction_min_role, Role.DRAFTER)
    if not ctx.at_least(required):  # type: ignore[arg-type]
        raise HTTPException(
            status_code=403, detail=f"Corrections require at least the {required} role"
        )
    with bound(ctx):
        try:
            item = memory.correct(
                item_id=item_id,
                new_content=body.new_content,
                reason=body.reason,
                source_type=SourceType.parse(body.source_type, SourceType.MANUAL),  # type: ignore[arg-type]
                confidence=body.confidence,
                settings=settings,
                ctx=ctx,
            )
        except memory.MemoryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return item.to_dict(include_versions=False)


@router.post("/versions/{version_id}/approve")
def approve_version(
    version_id: str, ctx: TenantContext = Depends(require_drafter)
) -> dict[str, Any]:
    """Mark an inferred (PENDING) memory version as human-approved."""
    with bound(ctx):
        try:
            return memory.approve_version(version_id, ctx=ctx).to_dict()
        except memory.MemoryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
