"""
Meaning-based memory search
===========================

Adds vector search to the memory store (freedombot/memory/store.py) without replacing any of it. Embeddings come
from Agno's ``OpenAIEmbedder`` and live in a workspace table (``memory_embeddings``) searched with pgvector.
The store keeps owning versions, approvals, conflicts and data areas: a vector hit is only a ranking signal,
found with the same visibility clauses as a keyword hit and redacted the same way before anyone sees it.

What is never embedded (it stays keyword-only, so its text is not sent to the embedding API ahead of a
question):

* items in a restricted data area, or classified CONFIDENTIAL / RESTRICTED;
* text the regulated-data classifier flags (health, child, biometric data) or that holds a card-like number;
* the restricted ``[[area:…]]`` sections of an otherwise general item (they are removed first).

Vector search is off when ``MEMORY_VECTOR_SEARCH`` is off, when no OpenAI key is set, or when the workspace
cannot use pgvector (for example a shared-database workspace, whose search_path is pinned to its own schema).
Every failure falls back to keyword search; none of them fails a request.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Optional, Sequence

from agno.utils.log import log_warning
from sqlalchemy import text

from freedombot import settings
from freedombot.tenancy.context import TenantContext
from freedombot.tenancy.registry import workspace_connection, workspace_location

DIMENSIONS = 1536
#: Cosine distance above which a vector hit is too far from the question to count.
MAX_DISTANCE = 0.75
#: Missing embeddings filled per search, in one embedding call.
BACKFILL_BATCH = 100

_CARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SECTION = re.compile(r"\[\[area:\s*[a-z_]+\s*\]\].*?\[\[/area\]\]", re.S | re.I)

_ready: dict[tuple[str, str], bool] = {}
_lock = threading.Lock()


def _ddl(client_id: str) -> list[str]:
    from freedombot.tenancy.schema import client_wall_ddl

    return [
        f"""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            version_id  TEXT PRIMARY KEY REFERENCES memory_versions(id) ON DELETE CASCADE,
            item_id     TEXT NOT NULL,
            model       TEXT NOT NULL,
            embedding   vector({DIMENSIONS}) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS memory_embeddings_item_idx ON memory_embeddings (item_id)",
        *client_wall_ddl(client_id, ("memory_embeddings",)),
    ]


# --------------------------------------------------------------------------- embedder (Agno)

def _default_embed(texts: Sequence[str]) -> list[list[float]]:
    """One embedding call for a batch of texts, through Agno's OpenAI embedder."""
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    embedder = OpenAIEmbedder(id=settings.embedding_model_id(), dimensions=DIMENSIONS)
    response = embedder.client.embeddings.create(
        input=list(texts), model=embedder.id, dimensions=embedder.dimensions, encoding_format="float"
    )
    return [list(item.embedding) for item in sorted(response.data, key=lambda d: d.index)]


#: Replaced in tests; never calls a real API there.
embed: Callable[[Sequence[str]], list[list[float]]] = _default_embed


# --------------------------------------------------------------------------- availability

def enabled() -> bool:
    return settings.memory_vector_search() and settings.llm_available()


def available(tenant: Optional[TenantContext]) -> bool:
    """Whether this workspace can run vector search; prepares the table on first use."""
    if tenant is None or not enabled():
        return False
    db_name, db_schema = workspace_location(tenant)
    key = (db_name, db_schema)
    if key in _ready:
        return _ready[key]
    with _lock:
        if key not in _ready:
            _ready[key] = _prepare(tenant, db_schema)
    return _ready[key]


def _prepare(tenant: TenantContext, db_schema: str) -> bool:
    try:
        with workspace_connection(tenant) as conn:
            has_type = conn.execute(text("SELECT to_regtype('vector') IS NOT NULL")).scalar()
            if not has_type and db_schema == "public":
                # Only a workspace with its own database installs the extension: in a shared database it
                # would land in one tenant's schema.
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                has_type = conn.execute(text("SELECT to_regtype('vector') IS NOT NULL")).scalar()
            if not has_type:
                return False
            for statement in _ddl(tenant.client_id):
                conn.execute(text(statement))
        return True
    except Exception as exc:  # noqa: BLE001 - keyword search still works
        log_warning(f"memory vector search unavailable: {type(exc).__name__}: {exc}")
        return False


def reset_cache() -> None:
    _ready.clear()


# --------------------------------------------------------------------------- what may be embedded

def embeddable_text(title: str, content: str, *, access_area: Optional[str], classification: str) -> Optional[str]:
    """The text to embed, or None when the item must stay keyword-only."""
    from freedombot.policy.classifier import classify_text

    if access_area or str(classification).upper() in ("CONFIDENTIAL", "RESTRICTED"):
        return None
    body = _SECTION.sub(" ", content or "")
    blob = f"{title or ''}\n{body}".strip()
    if not blob:
        return None
    found = classify_text(blob)
    if found.categories or found.contains_phi or found.contains_card_data or found.contains_child_data \
            or found.contains_biometrics or _CARD_LIKE.search(blob):
        return None
    return blob[:8000]


# --------------------------------------------------------------------------- indexing and search

def index_missing(tenant: TenantContext, *, limit: int = BACKFILL_BATCH) -> int:
    """Embed active versions that have no embedding yet (one embedding call). Returns how many were added."""
    if not available(tenant):
        return 0
    model = settings.embedding_model_id()
    try:
        with workspace_connection(tenant) as conn:
            rows = conn.execute(
                text(
                    "SELECT v.id AS version_id, i.id AS item_id, i.title, i.access_area, i.classification, "
                    "v.content FROM memory_versions v JOIN memory_items i ON i.id = v.item_id "
                    "LEFT JOIN memory_embeddings e ON e.version_id = v.id AND e.model = :m "
                    "WHERE v.status = 'ACTIVE' AND e.version_id IS NULL "
                    "AND i.access_area IS NULL ORDER BY v.created_at DESC LIMIT :n"
                ),
                {"m": model, "n": max(1, limit) * 2},
            ).fetchall()
        todo = []
        for r in rows:
            blob = embeddable_text(r.title, r.content, access_area=r.access_area, classification=r.classification)
            if blob:
                todo.append((r.version_id, r.item_id, blob))
            if len(todo) >= limit:
                break
        if not todo:
            return 0
        vectors = embed([blob for _, _, blob in todo])
        with workspace_connection(tenant) as conn:
            for (version_id, item_id, _), vector in zip(todo, vectors):
                if len(vector) != DIMENSIONS:
                    continue
                conn.execute(
                    text(
                        "INSERT INTO memory_embeddings (version_id, item_id, model, embedding) "
                        "VALUES (:v, :i, :m, CAST(:e AS vector)) ON CONFLICT (version_id) DO NOTHING"
                    ),
                    {"v": version_id, "i": item_id, "m": model, "e": _literal(vector)},
                )
        return len(todo)
    except Exception as exc:  # noqa: BLE001 - search goes on with what is indexed
        log_warning(f"memory embedding backfill failed: {type(exc).__name__}: {exc}")
        return 0


def nearest(conn: Any, query: str, where_sql: str, params: dict[str, Any], limit: int) -> list[Any]:
    """Visible rows nearest to ``query`` in meaning, using the caller's own WHERE clauses.

    Rows carry ``v_id`` like the store's keyword query. Returns [] on any failure.
    """
    try:
        vector = embed([query])[0]
        if len(vector) != DIMENSIONS:
            return []
        rows = conn.execute(
            text(
                "SELECT i.*, v.id AS v_id, (e.embedding <=> CAST(:qvec AS vector)) AS distance "
                "FROM memory_items i JOIN memory_versions v ON v.item_id = i.id "
                "JOIN memory_embeddings e ON e.version_id = v.id AND e.model = :emodel "
                f"WHERE {where_sql} ORDER BY distance LIMIT :vlimit"
            ),
            {**params, "qvec": _literal(vector), "emodel": settings.embedding_model_id(), "vlimit": limit},
        ).fetchall()
        return [r for r in rows if r.distance is not None and r.distance <= MAX_DISTANCE]
    except Exception as exc:  # noqa: BLE001 - keyword results stand alone
        log_warning(f"memory vector search failed: {type(exc).__name__}: {exc}")
        return []


def fuse(keyword: list[Any], vector: list[Any], limit: int) -> list[Any]:
    """Reciprocal-rank fusion of two ranked row lists (keyed by version id), keyword first on ties."""
    scores: dict[str, float] = {}
    rows: dict[str, Any] = {}
    for weight, ranked in ((1.0, keyword), (1.0, vector)):
        for rank, row in enumerate(ranked):
            rows.setdefault(row.v_id, row)
            scores[row.v_id] = scores.get(row.v_id, 0.0) + weight / (60 + rank)
    order = {r.v_id: i for i, r in enumerate(keyword)}
    ranked_ids = sorted(scores, key=lambda v: (-scores[v], order.get(v, len(order))))
    return [rows[v] for v in ranked_ids[:limit]]


def _literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in vector) + "]"
