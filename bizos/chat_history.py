"""
Chat History (M01-11)
=====================

Stores each chat turn exactly as the user saw it, inside the client's own
workspace, so a conversation survives a refresh and can be reopened later.

Two rules:

* **Per workspace.** The table lives in the client's database or schema with
  the same ``client_id`` wall as every other workspace table, so one client's
  history is unreachable from another.
* **Per user.** A conversation belongs to the person who started it. Listing
  and reading are filtered by ``user_id``, and continuing someone else's
  ``session_id`` is refused.

What is stored is the *post-enforcement* reply (persona fixed, restricted
content removed). Agno's own run log keeps the raw model output and is never
shown to users.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from sqlalchemy import text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import workspace_connection, workspace_location
from bizos.tenancy.schema import CHAT_MESSAGES_DDL, client_wall_ddl
from bizos.util.ids import new_id

#: Workspaces already confirmed to have the table in this process.
_ready: set[tuple[str, str]] = set()
_lock = threading.Lock()


class SessionNotFound(LookupError):
    """No conversation with that id belongs to the caller."""


def ensure_table(ctx: TenantContext) -> None:
    """Create ``chat_messages`` in workspaces provisioned before it existed.

    Idempotent and cached per workspace, so it costs one round trip per process.
    """
    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            for statement in CHAT_MESSAGES_DDL:
                conn.execute(text(statement))
            for statement in client_wall_ddl(ctx.client_id, ("chat_messages",)):
                conn.execute(text(statement))
        _ready.add(key)


def owner_of(ctx: TenantContext, session_id: str) -> Optional[str]:
    """The user who started ``session_id`` in this workspace, if anyone has."""
    ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        row = conn.execute(
            text("SELECT user_id FROM chat_messages WHERE session_id = :s ORDER BY created_at LIMIT 1"),
            {"s": session_id},
        ).first()
    return row.user_id if row else None


def assert_can_continue(ctx: TenantContext, session_id: str) -> None:
    """Refuse to append to a conversation another user started."""
    owner = owner_of(ctx, session_id)
    if owner is not None and owner != ctx.user_id:
        raise SessionNotFound(session_id)


def record_turn(
    ctx: TenantContext,
    *,
    session_id: str,
    user_message: str,
    reply: str,
    domain: Optional[str],
    meta: dict[str, Any],
) -> None:
    """Append one user message and the reply that was shown for it."""
    ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        for role, content, extra in (
            ("user", user_message, {}),
            ("assistant", reply, meta),
        ):
            conn.execute(
                text(
                    "INSERT INTO chat_messages (id, session_id, user_id, role, content, domain, meta) "
                    "VALUES (:i, :s, :u, :r, :c, :d, CAST(:m AS JSONB))"
                ),
                {
                    "i": new_id("msg"),
                    "s": session_id,
                    "u": ctx.user_id,
                    "r": role,
                    "c": content,
                    "d": domain if role == "assistant" else None,
                    "m": json.dumps(extra, default=str),
                },
            )


def list_sessions(ctx: TenantContext, *, limit: int = 50) -> list[dict[str, Any]]:
    """The caller's conversations, newest activity first."""
    ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                """
                SELECT session_id,
                       MIN(created_at) AS started_at,
                       MAX(created_at) AS updated_at,
                       COUNT(*) FILTER (WHERE role = 'user') AS turns,
                       (ARRAY_AGG(content ORDER BY created_at) FILTER (WHERE role = 'user'))[1] AS first_message
                FROM chat_messages
                WHERE user_id = :u
                GROUP BY session_id
                ORDER BY MAX(created_at) DESC
                LIMIT :l
                """
            ),
            {"u": ctx.user_id, "l": max(1, min(limit, 200))},
        ).fetchall()
    return [
        {
            "session_id": r.session_id,
            "title": _title(r.first_message),
            "turns": int(r.turns or 0),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


def get_session(ctx: TenantContext, session_id: str) -> dict[str, Any]:
    """Every message of one of the caller's conversations, oldest first."""
    ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT role, content, domain, meta, created_at FROM chat_messages "
                "WHERE session_id = :s AND user_id = :u ORDER BY created_at, id"
            ),
            {"s": session_id, "u": ctx.user_id},
        ).fetchall()
    if not rows:
        raise SessionNotFound(session_id)
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": r.role,
                "content": r.content,
                "domain": r.domain,
                "meta": r.meta or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def _title(first_message: Optional[str]) -> str:
    text_ = " ".join((first_message or "").split())
    return (text_[:77] + "…") if len(text_) > 80 else (text_ or "Conversation")
