"""Organizational memory routes (§26 /api/memory)."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, require_admin, require_approver, require_drafter
from freedombot.control.models import ClientSettings
from freedombot.memory import documents
from freedombot.memory import store as memory
from freedombot.tenancy.context import TenantContext
from freedombot.types import ApprovalStatus, AuditEventType, MemoryCategory, Role, SourceType

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _refuse_regulated_data(text: str, settings: ClientSettings, ctx: TenantContext) -> None:
    """A value typed into a memory form gets the same check as "Remember that ..." typed in chat."""
    from app.api.chat import _regulated_data_refusal
    from freedombot.audit.events import log

    refusal = _regulated_data_refusal(f"Remember that {text}", settings)
    if refusal:
        log(AuditEventType.GUARDRAIL_TRIGGERED, ctx=ctx, status=f"MEMORY_{refusal[0]}")
        raise HTTPException(status_code=422, detail=refusal[1])


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


class MemoryArchive(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


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


@router.get("/documents")
def list_documents(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Uploaded knowledge-base documents this user can see (one row per document)."""
    with bound(ctx):
        return {"items": documents.list_documents(ctx=ctx)}


@router.post("/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    access_area: str = Form(""),
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Admin only: add a PDF or text file to the knowledge base, visible to everyone or to one data area."""
    data = await file.read(documents.MAX_BYTES + 1)
    with bound(ctx):
        try:
            return documents.upload(title=title, filename=file.filename or "document", data=data,
                                    access_area=access_area or None, settings=settings, ctx=ctx)
        except documents.DocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/documents/{document_id}/text")
def read_document_text(document_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """The text of a document this user can see."""
    with bound(ctx):
        found = documents.document_text(document_id, ctx=ctx)
    if found is None:
        raise HTTPException(status_code=404, detail="No such document")
    return found


@router.get("/documents/{document_id}/file")
def read_document_file(document_id: str, ctx: TenantContext = Depends(current_context)) -> Response:
    """The original file of a document this user can see. Only a PDF opens in the browser."""
    with bound(ctx):
        found = documents.document_file(document_id, ctx=ctx)
    if found is None:
        raise HTTPException(status_code=404, detail="No such file")
    disposition = "inline" if found["content_type"] == "application/pdf" else "attachment"
    name = quote(found["filename"])
    return Response(
        content=found["data"],
        media_type=found["content_type"],
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{name}", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/documents/{document_id}/archive")
def archive_document(document_id: str, ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    """Admin only: remove a document from the knowledge base (its parts are archived, history kept)."""
    with bound(ctx):
        archived = documents.archive_document(document_id, reason="Removed by an admin", ctx=ctx)
    if not archived:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document_id": document_id, "parts_archived": archived}


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
        _refuse_regulated_data(f"{body.title}: {body.content}", settings, ctx)
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
        _refuse_regulated_data(body.new_content, settings, ctx)
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


@router.post("/{item_id}/archive")
def archive_memory(
    item_id: str, body: MemoryArchive, ctx: TenantContext = Depends(require_approver)
) -> dict[str, Any]:
    """Remove an item from use. Its history is kept for audit; nothing is erased.

    Approver or Admin only: removing a recorded policy changes what the
    assistant tells everyone in the workspace.
    """
    with bound(ctx):
        try:
            return memory.archive(item_id, reason=body.reason, ctx=ctx).to_dict(include_versions=True)
        except memory.MemoryNotFound as exc:
            raise HTTPException(status_code=404, detail="Memory item not found or already removed") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
