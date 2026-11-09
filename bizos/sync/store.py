"""Synced records and sync checkpoints, in the customer's workspace database (owner-only)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import readonly_connection, workspace_connection, workspace_location
from bizos.util.timeutil import utcnow

SOURCES = ("gmail", "calendar", "youtube", "hubspot")
#: The access tag each source needs. A record synced for someone without the tag stays hidden from them.
SOURCE_AREAS = {"gmail": "email", "calendar": "calendar", "hubspot": "crm"}

_ready: set[tuple[str, str]] = set()
_lock = threading.Lock()


@dataclass
class SyncItem:
    """One record from a source, normalized for storage and search."""

    source: str
    kind: str
    source_id: str
    title: str
    body: str
    occurred_at: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        blob = json.dumps([self.kind, self.title, self.body, self.occurred_at, self.data], sort_keys=True,
                          default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _ddl(client_id: str) -> list[str]:
    from bizos.tenancy.schema import client_wall_ddl

    return [
        """
        CREATE TABLE IF NOT EXISTS synced_items (
            id              TEXT PRIMARY KEY,
            owner_user_id   TEXT NOT NULL,
            source          TEXT NOT NULL,
            kind            TEXT NOT NULL,
            source_id       TEXT NOT NULL,
            title           TEXT NOT NULL DEFAULT '',
            body            TEXT NOT NULL DEFAULT '',
            occurred_at     TIMESTAMPTZ,
            data            JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_hash    TEXT NOT NULL,
            first_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT synced_items_unique UNIQUE (owner_user_id, source, source_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS synced_items_owner_idx ON synced_items (owner_user_id, occurred_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            owner_user_id TEXT NOT NULL,
            source        TEXT NOT NULL,
            checkpoint    TEXT,
            last_run_at   TIMESTAMPTZ,
            last_ok_at    TIMESTAMPTZ,
            last_error    TEXT,
            items_new     INTEGER NOT NULL DEFAULT 0,
            items_updated INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (owner_user_id, source)
        )
        """,
        *client_wall_ddl(client_id, ("synced_items", "sync_state")),
    ]


def ensure_tables(ctx: TenantContext) -> None:
    """Create the sync tables in this workspace once per process."""
    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            for statement in _ddl(ctx.client_id):
                conn.execute(text(statement))
        _ready.add(key)


def _row_id(owner: str, source: str, source_id: str) -> str:
    return "syn_" + hashlib.sha256(f"{owner}|{source}|{source_id}".encode("utf-8")).hexdigest()[:24]


def _when(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if raw.isdigit():  # Gmail internalDate (milliseconds)
        try:
            return datetime.fromtimestamp(int(raw) / 1000, tz=utcnow().tzinfo)
        except (ValueError, OSError):
            return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=utcnow().tzinfo)


def upsert(ctx: TenantContext, owner: str, items: Sequence[SyncItem]) -> tuple[int, int]:
    """Insert new records and update changed ones. Returns (new, updated); unchanged ones are skipped."""
    ensure_tables(ctx)
    new = updated = 0
    with workspace_connection(ctx) as conn:
        for item in items:
            if not item.source_id:
                continue
            digest = item.content_hash()
            params = {
                "id": _row_id(owner, item.source, item.source_id), "o": owner, "s": item.source, "k": item.kind,
                "sid": item.source_id, "t": item.title[:500], "b": item.body[:8000], "at": _when(item.occurred_at),
                "d": json.dumps(item.data, default=str), "h": digest,
            }
            row = conn.execute(
                text("SELECT content_hash FROM synced_items WHERE id = :id"), {"id": params["id"]}
            ).first()
            if row is None:
                conn.execute(
                    text(
                        "INSERT INTO synced_items (id, owner_user_id, source, kind, source_id, title, body, "
                        "occurred_at, data, content_hash) VALUES (:id, :o, :s, :k, :sid, :t, :b, :at, "
                        "CAST(:d AS JSONB), :h)"
                    ),
                    params,
                )
                new += 1
            elif row.content_hash != digest:
                conn.execute(
                    text(
                        "UPDATE synced_items SET kind = :k, title = :t, body = :b, occurred_at = :at, "
                        "data = CAST(:d AS JSONB), content_hash = :h, updated_at = NOW() WHERE id = :id"
                    ),
                    params,
                )
                updated += 1
    return new, updated


def get_checkpoint(ctx: TenantContext, owner: str, source: str) -> Optional[str]:
    ensure_tables(ctx)
    with readonly_connection(ctx) as conn:
        row = conn.execute(
            text("SELECT checkpoint FROM sync_state WHERE owner_user_id = :o AND source = :s"),
            {"o": owner, "s": source},
        ).first()
    return row.checkpoint if row else None


def record_run(ctx: TenantContext, owner: str, source: str, *, checkpoint: Optional[str], new: int = 0,
               updated: int = 0, error: Optional[str] = None) -> None:
    ensure_tables(ctx)
    with workspace_connection(ctx) as conn:
        conn.execute(
            text(
                "INSERT INTO sync_state (owner_user_id, source, checkpoint, last_run_at, last_ok_at, last_error, "
                "items_new, items_updated) VALUES (:o, :s, :c, NOW(), CASE WHEN CAST(:e AS TEXT) IS NULL THEN NOW() END, "
                "CAST(:e AS TEXT), :n, :u) ON CONFLICT (owner_user_id, source) DO UPDATE SET "
                "checkpoint = COALESCE(EXCLUDED.checkpoint, sync_state.checkpoint), last_run_at = NOW(), "
                "last_ok_at = CASE WHEN EXCLUDED.last_error IS NULL THEN NOW() ELSE sync_state.last_ok_at END, "
                "last_error = EXCLUDED.last_error, items_new = EXCLUDED.items_new, "
                "items_updated = EXCLUDED.items_updated"
            ),
            {"o": owner, "s": source, "c": checkpoint, "e": (error or None) and error[:500], "n": new, "u": updated},
        )


def forget(ctx: TenantContext, owner: str, source: str) -> int:
    """Delete one user's synced records and state for a source (on disconnect)."""
    ensure_tables(ctx)
    with workspace_connection(ctx) as conn:
        removed = conn.execute(
            text("DELETE FROM synced_items WHERE owner_user_id = :o AND source = :s"), {"o": owner, "s": source}
        ).rowcount
        conn.execute(text("DELETE FROM sync_state WHERE owner_user_id = :o AND source = :s"),
                     {"o": owner, "s": source})
    return removed


_WORD = re.compile(r"[a-z0-9@._+-]{3,}", re.I)
_STOP = frozenset({"the", "and", "for", "from", "with", "what", "any", "about", "this", "that", "have", "has",
                   "was", "are", "new", "latest", "recent", "show", "find", "my", "me", "any", "did", "get"})


def search(ctx: TenantContext, query: str = "", *, sources: Optional[Sequence[str]] = None, since: Optional[str] = None,
           limit: int = 20) -> list[dict[str, Any]]:
    """The caller's own synced records matching ``query`` (any meaningful word), newest first."""
    picked = [s for s in (sources or []) if s in SOURCES] or list(SOURCES)
    allowed = [s for s in picked if ctx.can_see_area(SOURCE_AREAS.get(s))]
    if not allowed:
        return []
    ensure_tables(ctx)
    clauses = ["owner_user_id = :o", "source = ANY(:sources)"]
    params: dict[str, Any] = {"o": ctx.user_id, "sources": allowed, "limit": max(1, min(int(limit or 20), 50))}
    when = _when(since)
    if when is not None:
        clauses.append("occurred_at >= :since")
        params["since"] = when
    words = [w for w in _WORD.findall(query or "") if w.lower() not in _STOP][:6]
    if words:
        ors = []
        for n, word in enumerate(words):
            params[f"w{n}"] = f"%{word}%"
            ors.append(f"(title ILIKE :w{n} OR body ILIKE :w{n})")
        clauses.append("(" + " OR ".join(ors) + ")")
    with readonly_connection(ctx) as conn:
        rows = conn.execute(
            text(
                "SELECT source, kind, source_id, title, body, occurred_at, data, updated_at FROM synced_items "
                f"WHERE {' AND '.join(clauses)} ORDER BY occurred_at DESC NULLS LAST, updated_at DESC LIMIT :limit"
            ),
            params,
        ).fetchall()
    return [
        {
            "source": r.source, "kind": r.kind, "id": r.source_id, "title": r.title, "body": r.body[:1200],
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "details": r.data if isinstance(r.data, dict) else {},
            "_untrusted_source": f"synced_{r.source}",
        }
        for r in rows
    ]


def status(ctx: TenantContext) -> list[dict[str, Any]]:
    """Per-source sync state and record counts for the caller."""
    ensure_tables(ctx)
    with readonly_connection(ctx) as conn:
        states = {
            r.source: r for r in conn.execute(
                text("SELECT * FROM sync_state WHERE owner_user_id = :o"), {"o": ctx.user_id}
            )
        }
        counts = {
            r.source: r.n for r in conn.execute(
                text("SELECT source, count(*) AS n FROM synced_items WHERE owner_user_id = :o GROUP BY source"),
                {"o": ctx.user_id},
            )
        }
    out = []
    for source in SOURCES:
        state = states.get(source)
        out.append({
            "source": source,
            "items": int(counts.get(source, 0)),
            "last_run_at": state.last_run_at.isoformat() if state and state.last_run_at else None,
            "last_ok_at": state.last_ok_at.isoformat() if state and state.last_ok_at else None,
            "last_error": state.last_error if state else None,
            "items_new": state.items_new if state else 0,
            "items_updated": state.items_updated if state else 0,
        })
    return out
