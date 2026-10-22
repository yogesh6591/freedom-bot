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
content removed), with regulated identifiers (SSN, card and medical record
numbers, dates of birth) replaced by ``[REDACTED]``. Agno's own run log keeps the raw model output and is never
shown to users.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from sqlalchemy import bindparam, text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import workspace_connection, workspace_location
from bizos.tenancy.schema import CHAT_MESSAGES_DDL, client_wall_ddl
from bizos.util.ids import new_id
from bizos.util.redaction import scrub_identifiers

#: Workspaces already confirmed to have the table in this process.
_ready: set[tuple[str, str]] = set()
_lock = threading.Lock()


class SessionNotFound(LookupError):
    """No conversation with that id belongs to the caller."""


class NotUserMessage(ValueError):
    """The target message exists but is not a user message."""


#: Message ids hidden from ``get_session`` for the current edit rerun, so the
#: model sees only history before the edited message while those rows still
#: exist (they are deleted only after the new reply is stored).
_hidden_ids: ContextVar[Optional[frozenset[str]]] = ContextVar("chat_hidden_ids", default=None)


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
                    # M07-20: never keep an SSN, card or record number verbatim.
                    "c": scrub_identifiers(content),
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


def set_latest_email_draft(
    ctx: TenantContext,
    session_id: str,
    email_draft: str,
    extra: dict | None = None,
) -> None:
    """Replace the email on the latest assistant turn with what the user edited.

    Both the display trace (``meta.json``) and the committed state the next turn reads
    (``meta.state.draft``) are updated, so the email that is sent is the email that was edited.
    """
    ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        row = conn.execute(
            text(
                "SELECT id, meta FROM chat_messages "
                "WHERE session_id = :s AND user_id = :u AND role = 'assistant' "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"s": session_id, "u": ctx.user_id},
        ).first()
        if row is None:
            raise SessionNotFound(session_id)
        meta = row.meta or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not isinstance(meta, dict):
            meta = {}
        keys = ("email_subject", "email_body", "email_from", "email_address", "email_view")
        payload = meta.get("json") if isinstance(meta.get("json"), dict) else {}
        payload["email_draft"] = email_draft
        for key in keys:
            if extra and extra.get(key) is not None:
                payload[key] = extra[key]
        meta["json"] = payload
        state = meta.get("state")
        if isinstance(state, dict) and isinstance(state.get("draft"), dict):
            draft = state["draft"]
            draft["email_draft"] = email_draft
            for key in ("email_subject", "email_body", "email_from"):
                if extra and extra.get(key):
                    draft[key] = extra[key]
            meta["state"] = state
        if extra and isinstance(extra.get("people"), dict) and isinstance(state, dict):
            rows = state.get("recipients")
            if isinstance(rows, list):
                patches = extra["people"]
                for recipient in rows:
                    if not isinstance(recipient, dict):
                        continue
                    patch = patches.get(str(recipient.get("email") or "").strip()) or patches.get(
                        str(recipient.get("email") or "").strip().lower()
                    )
                    if not isinstance(patch, dict):
                        continue
                    first = str(patch.get("first_name") or "").strip()
                    last = str(patch.get("last_name") or "").strip()
                    name = " ".join(part for part in (first, last) if part)
                    if name:
                        recipient["name"] = name
                state["recipients"] = rows
                meta["state"] = state
        conn.execute(
            text("UPDATE chat_messages SET meta = CAST(:m AS JSONB) WHERE id = :i"),
            {"m": json.dumps(meta, default=str), "i": row.id},
        )


def edit_latest_assistant_meta(ctx: TenantContext, session_id: str, mutate) -> Any:
    """Load the latest assistant message's meta, let ``mutate(meta)`` change it, and save it.

    Whatever ``mutate`` returns is handed back. If it raises, nothing is saved.
    """
    ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        row = conn.execute(
            text(
                "SELECT id, meta FROM chat_messages "
                "WHERE session_id = :s AND user_id = :u AND role = 'assistant' "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"s": session_id, "u": ctx.user_id},
        ).first()
        if row is None:
            raise SessionNotFound(session_id)
        meta = row.meta or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not isinstance(meta, dict):
            meta = {}
        result = mutate(meta)
        conn.execute(
            text("UPDATE chat_messages SET meta = CAST(:m AS JSONB) WHERE id = :i"),
            {"m": json.dumps(meta, default=str), "i": row.id},
        )
        return result


def delete_session(ctx: TenantContext, session_id: str) -> None:
    """Remove one of the caller's conversations."""
    ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        result = conn.execute(
            text("DELETE FROM chat_messages WHERE session_id = :s AND user_id = :u"),
            {"s": session_id, "u": ctx.user_id},
        )
        if not result.rowcount:
            raise SessionNotFound(session_id)


def delete_all_sessions(ctx: TenantContext) -> int:
    """Remove every conversation belonging to the caller in this workspace."""
    ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        result = conn.execute(text("DELETE FROM chat_messages WHERE user_id = :u"), {"u": ctx.user_id})
        return int(result.rowcount or 0)


def get_session(ctx: TenantContext, session_id: str) -> dict[str, Any]:
    """Every message of one of the caller's conversations, oldest first."""
    ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT id, role, content, domain, meta, created_at FROM chat_messages "
                "WHERE session_id = :s AND user_id = :u ORDER BY created_at, id"
            ),
            {"s": session_id, "u": ctx.user_id},
        ).fetchall()
    if not rows:
        raise SessionNotFound(session_id)
    hidden = _hidden_ids.get()
    messages = [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "domain": r.domain,
            "meta": r.meta or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
        if hidden is None or r.id not in hidden
    ]
    return {"session_id": session_id, "messages": messages}


@contextmanager
def excluding_messages(ids: list[str]) -> Iterator[None]:
    """Hide these rows from ``get_session`` in this thread until the block ends."""
    token = _hidden_ids.set(frozenset(ids))
    try:
        yield
    finally:
        _hidden_ids.reset(token)


def tail_from_user_message(ctx: TenantContext, session_id: str, message_id: str) -> list[str]:
    """Ids of this user message and every message after it, oldest first.

    Raises ``SessionNotFound`` if the conversation or message is not the caller's.
    Raises ``NotUserMessage`` if the target exists but is not a user message.
    """
    ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT id, role FROM chat_messages "
                "WHERE session_id = :s AND user_id = :u ORDER BY created_at, id"
            ),
            {"s": session_id, "u": ctx.user_id},
        ).fetchall()
    if not rows:
        raise SessionNotFound(session_id)
    ids = [r.id for r in rows]
    try:
        index = ids.index(message_id)
    except ValueError as exc:
        raise SessionNotFound(session_id) from exc
    if rows[index].role != "user":
        raise NotUserMessage(message_id)
    return ids[index:]


def delete_messages(ctx: TenantContext, session_id: str, ids: list[str]) -> None:
    """Remove specific messages the caller owns in this conversation."""
    if not ids:
        return
    ensure_table(ctx)
    stmt = text(
        "DELETE FROM chat_messages WHERE session_id = :s AND user_id = :u AND id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    with workspace_connection(ctx) as conn:
        conn.execute(stmt, {"s": session_id, "u": ctx.user_id, "ids": list(ids)})


def _title(first_message: Optional[str]) -> str:
    text_ = " ".join((first_message or "").split())
    return (text_[:77] + "…") if len(text_) > 80 else (text_ or "Conversation")
