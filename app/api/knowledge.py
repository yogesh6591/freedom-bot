"""Knowledge routes (§26 /api/knowledge)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.api.deps import bound, client_settings, current_context, require_drafter, run_scope
from bizos.control.models import ClientSettings
from bizos.knowledge import service
from bizos.tenancy.context import TenantContext

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

#: Extensions agno has readers for and that this deployment accepts.
ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class UrlIngest(BaseModel):
    name: str
    url: str


@router.get("")
def list_content(
    limit: int = Query(default=100, le=500), ctx: TenantContext = Depends(current_context)
) -> dict[str, Any]:
    """Uploaded documents with agno's ingestion/indexing status."""
    with bound(ctx):
        return {
            "items": service.list_content(ctx, limit=limit),
            "vector_search_available": service.get_knowledge(ctx) is not None,
        }


@router.get("/search")
def search(
    q: str,
    limit: int = Query(default=5, le=50),
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    with bound(ctx):
        scope = run_scope(ctx, settings)
        result = service.search_knowledge(scope, q, limit=limit)
        return {
            "items": result.data,
            "untrusted_findings": result.untrusted_findings,
            "summary": result.summary,
        }


@router.post("/upload", status_code=202)
async def upload(
    file: UploadFile = File(...),
    name: Optional[str] = Form(default=None),
    ctx: TenantContext = Depends(require_drafter),
) -> dict[str, Any]:
    """Ingest an uploaded document into this workspace's knowledge base."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {suffix!r}. Allowed: {sorted(ALLOWED_SUFFIXES)}",
        )

    # Written to a temporary file rather than held in memory, and size-capped so a
    # large upload cannot exhaust the process.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        written = 0
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                Path(tmp.name).unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File exceeds the 25 MB limit")
            tmp.write(chunk)
        temp_path = tmp.name

    with bound(ctx):
        try:
            return service.add_content(
                ctx,
                name=name or file.filename or "document",
                path=temp_path,
                metadata={"uploaded_by": ctx.user_id, "filename": file.filename},
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            Path(temp_path).unlink(missing_ok=True)


@router.post("/url", status_code=202)
def ingest_url(body: UrlIngest, ctx: TenantContext = Depends(require_drafter)) -> dict[str, Any]:
    with bound(ctx):
        try:
            return service.add_content(ctx, name=body.name, url=body.url, metadata={"uploaded_by": ctx.user_id})
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
