"""
Ticket Context
==============

What a support ticket carries from the chat it was raised in, so nobody has to repeat the issue: the
question, the assistant's answer, a few turns before them, and who raised it in which workspace.

Built on the server from the caller's own saved conversation (:func:`freedombot.chat_history.get_session`
returns only their own sessions), never from text the browser sends, so a ticket cannot carry someone
else's chat.
"""

from __future__ import annotations

from typing import Any, Optional

from freedombot.tenancy.context import TenantContext

#: Earlier turns kept with a ticket, and how long each may be.
CONTEXT_TURNS = 6
_MAX_CHARS = 1500


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= _MAX_CHARS else text[: _MAX_CHARS - 1] + "…"


def who(ctx: TenantContext) -> dict[str, Any]:
    """The person and workspace a ticket came from."""
    from freedombot.control import store as control

    try:
        client = control.get_client(ctx.client_id)
        workspace = {"id": client.id, "name": client.company_name, "slug": client.slug}
    except Exception:  # noqa: BLE001 - a ticket is still worth raising without the workspace name
        workspace = {"id": ctx.client_id, "slug": ctx.client_slug}
    return {
        "workspace": workspace,
        "user": {"id": ctx.user_id, "email": ctx.email, "name": ctx.display_name,
                 "role": str(ctx.primary_role), "access_mode": ctx.access_mode},
    }


def chat_context(ctx: TenantContext, session_id: Optional[str], message_id: Optional[str] = None) -> dict[str, Any]:
    """The conversation a ticket was raised from. ``message_id`` is the flagged assistant answer.

    Raises :class:`freedombot.chat_history.SessionNotFound` for a session that is not the caller's,
    and ``LookupError`` for a message that is not in it.
    """
    out = who(ctx)
    if not session_id:
        return out
    from freedombot import chat_history

    messages = chat_history.get_session(ctx, session_id)["messages"]
    end = len(messages)
    if message_id:
        index = next((i for i, m in enumerate(messages) if m["id"] == message_id), None)
        if index is None or messages[index]["role"] != "assistant":
            raise LookupError(message_id)
        end = index + 1
        answer = messages[index]
        question = next((m for m in reversed(messages[:index]) if m["role"] == "user"), None)
        out["flagged_answer"] = _clip(answer["content"])
        out["question"] = _clip(question["content"]) if question else ""
        out["message_id"] = message_id
    out["session_id"] = session_id
    out["conversation"] = [
        {"role": m["role"], "content": _clip(m["content"]), "at": m.get("created_at")}
        for m in messages[max(0, end - CONTEXT_TURNS):end]
    ]
    return out
