"""Runs one due scheduled task as a chat turn of the person who set it up (freedombot/automation.py).

The instruction goes through the same chat pipeline as a typed message: router, guard, policy check,
approvals and audit. The reply is saved in the task's own conversation, so the person can open it in
chat history, and a short result is kept on the task row.
"""

from __future__ import annotations

from typing import Any

from freedombot.tenancy.context import TenantContext


def run_in_chat(ctx: TenantContext, settings: Any, task: dict[str, Any]) -> str:
    from app.api.chat import ChatRequest, _dispatch

    message = (f'Scheduled task "{task["title"]}" ({task["schedule"]}). Do this now, once: '
               f'{task["instruction"]}')
    response = _dispatch(ChatRequest(message=message, session_id=task.get("session_id")), ctx, settings)
    content = str(response.get("content") or "").strip()
    waiting = len(response.get("awaiting_approval") or [])
    note = f" ({waiting} change(s) waiting for approval)" if waiting else ""
    return (content[:1800] + note) if content else f"Ran{note}."
