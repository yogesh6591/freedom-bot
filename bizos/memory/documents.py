"""
Knowledge-base documents
========================

An admin uploads a PDF, Word or text file. Its text is split into parts and each part is stored as a memory
item in the ``DOCUMENT`` category, with the access area the admin chose. From then on the parts behave like
any other memory: ``memory_search`` finds them, and a user only ever sees parts whose area they can see (no
area means everyone in the workspace).

The original file is kept too, so people can open it. Reading it goes through the same parts: if a user cannot
see the parts, the file is reported as not found.
"""

from __future__ import annotations

import io
import re
import threading
from typing import Any, Optional

from sqlalchemy import text as sql

from bizos.audit.events import log
from bizos.memory import store, vectors
from bizos.policy.classifier import find_ssn
from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import workspace_connection, workspace_location
from bizos.types import ApprovalStatus, AuditEventType, DataClassification, MemoryCategory, SourceType
from bizos.util.ids import new_id

#: Accepted file types and the largest upload, in bytes.
EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
MAX_BYTES = 10 * 1024 * 1024
#: What each file type is served as. Decided here from the extension, never by what the uploader sent.
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}
#: Target size of one searchable part, in characters.
PART_CHARS = 1500


class DocumentError(ValueError):
    """The file cannot be used (type, size, or no readable text)."""


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".doc"):
        raise DocumentError("Old Word .doc files can't be read. Save it as .docx or PDF and upload it again.")
    if not name.endswith(EXTENSIONS):
        raise DocumentError(f"Only {', '.join(EXTENSIONS)} files can be uploaded.")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if name.endswith(".docx"):
        from bizos.chat_attachments import AttachmentError, extract

        try:
            text = extract(filename, data)["text"]
        except AttachmentError as exc:
            raise DocumentError(str(exc)) from exc
    elif name.endswith(".pdf"):
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # noqa: BLE001 - any parse failure is the file's problem
            raise DocumentError(f"The PDF could not be read: {exc}") from exc
    else:
        text = data.decode("utf-8", errors="replace")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise DocumentError("No text was found in the file (a scanned PDF has no text layer).")
    return text


def split(text: str, size: int = PART_CHARS) -> list[str]:
    """Parts of about ``size`` characters, broken at paragraph (or sentence) boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= size:
            pieces.append(para)
            continue
        sentence_parts = re.split(r"(?<=[.!?])\s+", para)
        buf = ""
        for sentence in sentence_parts:
            while len(sentence) > size:  # one very long sentence
                pieces.append(sentence[:size])
                sentence = sentence[size:]
            if len(buf) + len(sentence) + 1 > size and buf:
                pieces.append(buf)
                buf = ""
            buf = f"{buf} {sentence}".strip()
        if buf:
            pieces.append(buf)
    parts: list[str] = []
    buf = ""
    for piece in pieces:
        if len(buf) + len(piece) + 2 > size and buf:
            parts.append(buf)
            buf = ""
        buf = f"{buf}\n\n{piece}".strip()
    if buf:
        parts.append(buf)
    return parts


def upload(*, title: str, filename: str, data: bytes, access_area: Optional[str], settings: Any,
           ctx: TenantContext) -> dict[str, Any]:
    """Store a document as memory parts. Returns the document summary."""
    title = (title or "").strip() or re.sub(r"\.[a-z]+$", "", filename or "Document", flags=re.I)
    text = extract_text(filename, data)
    if find_ssn(text) and "ssn" not in (getattr(settings, "order_dpa_allows", None) or []):
        log(AuditEventType.GUARDRAIL_TRIGGERED, ctx=ctx, status="DOCUMENT_SSN_BLOCKED")
        raise DocumentError("This file has a Social Security number in it, so it was not uploaded. "
                            "Remove the number and upload it again.")
    parts = split(text)
    doc_id = new_id("doc")
    area = (access_area or "").strip().casefold() or None
    for n, content in enumerate(parts, 1):
        store.put(
            category=MemoryCategory.DOCUMENT,
            title=title if len(parts) == 1 else f"{title} (part {n} of {len(parts)})",
            content=content,
            memory_key=f"{doc_id}_p{n:03d}",
            tags=["document"],
            classification=DataClassification.CONFIDENTIAL if area else DataClassification.INTERNAL,
            attributes={"document_id": doc_id, "document_title": title, "filename": filename, "part": n,
                        "parts": len(parts), "topic": f"{doc_id}_p{n:03d}"},
            source_type=SourceType.DOCUMENT,
            source_id=doc_id,
            approval_status=ApprovalStatus.APPROVED,
            access_area=area,
            settings=settings,
            ctx=ctx,
        )
    _save_file(ctx, doc_id, filename, data)
    # Embed the new parts now (general-access documents only), so the first question about it is fast.
    for _ in range(5):
        if not vectors.index_missing(ctx):
            break
    return {"document_id": doc_id, "title": title, "filename": filename, "parts": len(parts),
            "access_area": area, "characters": sum(len(p) for p in parts)}


_ready: set[Any] = set()
_lock = threading.Lock()


def _ensure_files_table(ctx: TenantContext) -> None:
    """Create the file table on first use, so workspaces made before it existed get it too."""
    from bizos.tenancy.schema import client_wall_ddl

    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            conn.execute(sql(
                """
                CREATE TABLE IF NOT EXISTS memory_document_files (
                    document_id TEXT PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    size_bytes  INTEGER NOT NULL,
                    data        BYTEA NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ))
            for statement in client_wall_ddl(ctx.client_id, ("memory_document_files",)):
                conn.execute(sql(statement))
        _ready.add(key)


def _save_file(ctx: TenantContext, document_id: str, filename: str, data: bytes) -> None:
    _ensure_files_table(ctx)
    with workspace_connection(ctx) as conn:
        conn.execute(
            sql("INSERT INTO memory_document_files (document_id, filename, size_bytes, data) VALUES (:i, :f, :s, :d)"),
            {"i": document_id, "f": filename, "s": len(data), "d": data},
        )


def _stored_files(ctx: TenantContext) -> set[str]:
    _ensure_files_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        return {r.document_id for r in conn.execute(sql("SELECT document_id FROM memory_document_files"))}


def _parts_of(document_id: str, ctx: TenantContext) -> list[Any]:
    """The parts of one document this user can see, in reading order."""
    parts = [i for i in _all_parts(ctx) if ((i.current.attributes if i.current else {}) or {}).get("document_id") == document_id]
    return sorted(parts, key=lambda i: i.current.attributes.get("part", 0))


def document_text(document_id: str, *, ctx: TenantContext) -> Optional[dict[str, Any]]:
    """The stored text of a document, or ``None`` if this user cannot see it."""
    parts = _parts_of(document_id, ctx)
    if not parts:
        return None
    attrs = parts[0].current.attributes
    return {"document_id": document_id, "title": attrs.get("document_title"), "filename": attrs.get("filename"),
            "text": "\n\n".join(p.current.content for p in parts)}


def document_file(document_id: str, *, ctx: TenantContext) -> Optional[dict[str, Any]]:
    """The original file, or ``None`` if this user cannot see the document or no file was kept."""
    if not _parts_of(document_id, ctx):
        return None
    _ensure_files_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        row = conn.execute(
            sql("SELECT filename, data FROM memory_document_files WHERE document_id = :i"), {"i": document_id}
        ).first()
    if row is None:
        return None
    extension = next((e for e in CONTENT_TYPES if row.filename.lower().endswith(e)), "")
    return {"filename": row.filename, "data": bytes(row.data),
            "content_type": CONTENT_TYPES.get(extension, "application/octet-stream")}


def _all_parts(ctx: TenantContext) -> list[Any]:
    """Every DOCUMENT item this user can see (list_items returns at most 500 per call)."""
    items: list[Any] = []
    while True:
        page = store.list_items(category=MemoryCategory.DOCUMENT, limit=500, offset=len(items), ctx=ctx)
        items += page
        if len(page) < 500:
            return items


def list_documents(*, ctx: TenantContext) -> list[dict[str, Any]]:
    """Every document this user can see, one row per document (not per part)."""
    docs: dict[str, dict[str, Any]] = {}
    files = _stored_files(ctx)
    for item in _all_parts(ctx):
        attrs = (item.current.attributes if item.current else {}) or {}
        doc_id = attrs.get("document_id") or item.memory_key.split("_p")[0]
        row = docs.setdefault(doc_id, {
            "document_id": doc_id, "title": attrs.get("document_title") or item.title,
            "filename": attrs.get("filename"), "parts": 0, "access_area": item.access_area,
            "has_file": doc_id in files,
            "uploaded_by": item.created_by, "uploaded_at": item.created_at.isoformat() if item.created_at else None,
        })
        row["parts"] += 1
    return sorted(docs.values(), key=lambda d: d["uploaded_at"] or "", reverse=True)


def archive_document(document_id: str, *, reason: str, ctx: TenantContext) -> int:
    """Archive every part of one document. Returns how many parts were archived."""
    archived = 0
    for item in _all_parts(ctx):
        attrs = (item.current.attributes if item.current else {}) or {}
        if attrs.get("document_id") == document_id:
            store.archive(item.id, reason=reason, ctx=ctx)
            archived += 1
    if archived:
        _ensure_files_table(ctx)
        with workspace_connection(ctx) as conn:
            conn.execute(sql("DELETE FROM memory_document_files WHERE document_id = :i"), {"i": document_id})
    return archived
