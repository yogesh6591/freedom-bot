"""
Knowledge-base documents
========================

An admin uploads a PDF or text file. Its text is split into parts and each part is stored as a memory item
in the ``DOCUMENT`` category, with the access area the admin chose. From then on the parts behave like any
other memory: ``memory_search`` finds them, and a user only ever sees parts whose area they can see (no area
means everyone in the workspace).
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional

from bizos.memory import store
from bizos.tenancy.context import TenantContext
from bizos.types import ApprovalStatus, DataClassification, MemoryCategory, SourceType
from bizos.util.ids import new_id

#: Accepted file types and the largest upload, in bytes.
EXTENSIONS = (".pdf", ".txt", ".md")
MAX_BYTES = 10 * 1024 * 1024
#: Target size of one searchable part, in characters.
PART_CHARS = 1500


class DocumentError(ValueError):
    """The file cannot be used (type, size, or no readable text)."""


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if not name.endswith(EXTENSIONS):
        raise DocumentError(f"Only {', '.join(EXTENSIONS)} files can be uploaded.")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if name.endswith(".pdf"):
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
    parts = split(extract_text(filename, data))
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
    return {"document_id": doc_id, "title": title, "filename": filename, "parts": len(parts),
            "access_area": area, "characters": sum(len(p) for p in parts)}


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
    for item in _all_parts(ctx):
        attrs = (item.current.attributes if item.current else {}) or {}
        doc_id = attrs.get("document_id") or item.memory_key.split("_p")[0]
        row = docs.setdefault(doc_id, {
            "document_id": doc_id, "title": attrs.get("document_title") or item.title,
            "filename": attrs.get("filename"), "parts": 0, "access_area": item.access_area,
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
    return archived
