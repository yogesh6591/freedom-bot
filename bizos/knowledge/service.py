"""
Knowledge Base
==============

Thin wrapper over agno's ``Knowledge`` (§28: do not build custom vector search).
The platform's only additions are:

* binding the knowledge base to the **client's own workspace**, so documents and
  embeddings inherit the same isolation boundary as everything else, and
* neutralizing retrieved document text as untrusted content before it reaches the
  model (§20) — a poisoned PDF is exactly the injection vector the brief calls out.

Vector storage uses ``PgVector`` in the client's workspace when the ``vector``
extension is available; otherwise the platform degrades to a SQL substring search
over the same content rows rather than failing, so knowledge remains usable on a
stock Postgres.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from agno.utils.log import log_info, log_warning
from sqlalchemy import text

from bizos.connectors.base import ConnectorResult
from bizos.connectors.untrusted import wrap_many
from bizos.tenancy.agnodb import workspace_db
from bizos.tenancy.context import TenantContext, require_context
from bizos.tenancy.registry import readonly_connection, workspace_location
from bizos.types import AuditEventType

_knowledge: dict[str, Any] = {}
_lock = threading.Lock()

#: Table agno's Knowledge uses for content rows in each workspace.
CONTENT_TABLE = "agno_knowledge"
VECTOR_TABLE = "knowledge_vectors"


def vector_available(ctx: Optional[TenantContext] = None) -> bool:
    """Whether pgvector is installed in this workspace."""
    try:
        with readonly_connection(ctx) as conn:
            return (
                conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                ).first()
                is not None
            )
    except Exception:
        return False


def ensure_vector_extension(ctx: TenantContext) -> bool:
    """Try to enable pgvector in the workspace. Returns whether it is available."""
    from bizos.tenancy.registry import workspace_connection

    try:
        with workspace_connection(ctx) as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        return True
    except Exception as exc:
        log_warning(f"pgvector unavailable in workspace {ctx.client_slug}: {exc}")
        return False


def get_knowledge(ctx: Optional[TenantContext] = None) -> Optional[Any]:
    """The agno ``Knowledge`` instance for this workspace, or ``None``.

    Returns ``None`` when no embedder is configured (no ``OPENAI_API_KEY``) or
    pgvector is unavailable — callers fall back to keyword search.
    """
    tenant = ctx or require_context()
    cached = _knowledge.get(tenant.client_id)
    if cached is not None:
        return cached

    from bizos import settings

    if not settings.llm_available():
        return None
    if not vector_available(tenant) and not ensure_vector_extension(tenant):
        return None

    with _lock:
        cached = _knowledge.get(tenant.client_id)
        if cached is not None:
            return cached
        try:
            from agno.knowledge.knowledge import Knowledge
            from agno.vectordb.pgvector import PgVector

            db_name, db_schema = workspace_location(tenant)
            vector_db = PgVector(
                table_name=VECTOR_TABLE,
                db_url=settings.database_url(db_name),
                schema=db_schema,
            )
            knowledge = Knowledge(
                name=f"{tenant.client_slug}-knowledge",
                contents_db=workspace_db(tenant),
                vector_db=vector_db,
            )
            _knowledge[tenant.client_id] = knowledge
            log_info(f"knowledge base ready for {tenant.client_slug}")
            return knowledge
        except Exception as exc:
            log_warning(f"knowledge base unavailable for {tenant.client_slug}: {exc}")
            return None


def clear_cache() -> None:
    with _lock:
        _knowledge.clear()


def search_knowledge(scope: Any, query: str, *, limit: int = 5) -> ConnectorResult:
    """Search the workspace knowledge base, neutralizing what comes back."""
    ctx: TenantContext = scope.ctx
    knowledge = get_knowledge(ctx)
    records: list[dict[str, Any]] = []

    if knowledge is not None:
        try:
            documents = knowledge.search(query=query, max_results=limit)
            for doc in documents or []:
                records.append(
                    {
                        "id": getattr(doc, "id", "") or "",
                        "name": getattr(doc, "name", "") or "",
                        "content": getattr(doc, "content", "") or "",
                        "meta": getattr(doc, "meta_data", {}) or {},
                    }
                )
        except Exception as exc:
            log_warning(f"vector search failed, falling back to keyword search: {exc}")

    if not records:
        records = _keyword_search(ctx, query, limit)

    safe, findings = wrap_many(records, source="knowledge_document", text_field="content")
    from bizos.audit.events import log

    log(
        AuditEventType.KNOWLEDGE_LOOKUP,
        ctx=ctx,
        request={"query": query},
        result={"hits": len(safe)},
        status="OK",
    )
    return ConnectorResult(
        ok=True,
        data=safe,
        summary=f"{len(safe)} knowledge result(s) for {query!r}",
        untrusted_findings=findings,
    )


def _keyword_search(ctx: TenantContext, query: str, limit: int) -> list[dict[str, Any]]:
    """Substring fallback over agno's content rows when vectors are unavailable."""
    try:
        with readonly_connection(ctx) as conn:
            rows = conn.execute(
                text(
                    f"SELECT id, name, description, metadata FROM {CONTENT_TABLE} "
                    "WHERE name ILIKE :q OR description ILIKE :q "
                    "ORDER BY created_at DESC LIMIT :l"
                ),
                {"q": f"%{query}%", "l": max(1, min(limit, 20))},
            ).fetchall()
        return [
            {
                "id": r.id,
                "name": r.name or "",
                "content": r.description or "",
                "meta": r.metadata or {},
            }
            for r in rows
        ]
    except Exception:
        return []


def list_content(ctx: Optional[TenantContext] = None, *, limit: int = 100) -> list[dict[str, Any]]:
    """Uploaded documents with their ingestion status, for the Knowledge UI."""
    try:
        with readonly_connection(ctx) as conn:
            rows = conn.execute(
                text(
                    f"SELECT id, name, description, type, size, status, status_message, "
                    f"created_at, updated_at FROM {CONTENT_TABLE} "
                    "ORDER BY created_at DESC LIMIT :l"
                ),
                {"l": max(1, min(limit, 500))},
            ).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as exc:
        log_warning(f"knowledge content listing failed: {exc}")
        return []


def add_content(
    ctx: TenantContext,
    *,
    name: str,
    path: Optional[str] = None,
    url: Optional[str] = None,
    text_content: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Ingest a document. Reuses agno's readers, chunking and status tracking."""
    knowledge = get_knowledge(ctx)
    if knowledge is None:
        raise RuntimeError(
            "The knowledge base is not available for this workspace. It needs an embedding "
            "model (OPENAI_API_KEY) and the pgvector extension."
        )
    if path:
        knowledge.insert(name=name, path=path, metadata=metadata or {})
    elif url:
        knowledge.insert(name=name, url=url, metadata=metadata or {})
    elif text_content is not None:
        knowledge.insert(name=name, text_content=text_content, metadata=metadata or {})
    else:
        raise ValueError("One of path, url or text_content is required")
    return {"name": name, "status": "queued"}
