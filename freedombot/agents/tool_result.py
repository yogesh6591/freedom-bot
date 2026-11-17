"""
Tool Result Contract
====================

The shape the v2 reply composer reads (docs/agent/ARCHITECTURE.md §5.10). Tools keep returning
:class:`~freedombot.tools.base.ToolOutcome`; :func:`from_outcome` wraps it without changing any tool.

Mapping from the policy check:
    ALLOW + ok        -> success
    ALLOW + not ok    -> error
    REQUIRE_APPROVAL  -> pending / approval   (ref = the approval request's action id)
    DRAFT_ONLY        -> pending / draft      (ref = the draft's action id)
    DENY              -> error
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from freedombot.tools.base import ToolOutcome
from freedombot.types import PolicyEffect


class ToolResult(BaseModel):
    tool: str
    status: Literal["success", "error", "pending"]
    pending_kind: Optional[Literal["approval", "draft"]] = None
    #: One line, human-readable.
    summary: str
    #: Record or action id, when there is one.
    ref: Optional[str] = None
    url: Optional[str] = None
    data: Any = None


def _first_line(text: str) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def from_outcome(outcome: ToolOutcome) -> ToolResult:
    """Wrap one guarded call's outcome in the reply contract."""
    summary = _first_line(outcome.message)
    if outcome.effect == PolicyEffect.REQUIRE_APPROVAL:
        return ToolResult(tool=outcome.tool, status="pending", pending_kind="approval", summary=summary,
                          ref=outcome.action_id)
    if outcome.effect == PolicyEffect.DRAFT_ONLY:
        return ToolResult(tool=outcome.tool, status="pending", pending_kind="draft", summary=summary,
                          ref=outcome.action_id, data=outcome.data)
    if outcome.effect == PolicyEffect.ALLOW and outcome.ok:
        return ToolResult(tool=outcome.tool, status="success", summary=summary, ref=_record_id(outcome.data),
                          data=outcome.data)
    return ToolResult(tool=outcome.tool, status="error", summary=summary)


def _record_id(data: Any) -> Optional[str]:
    """The id of the record a write produced, when the tool returned one."""
    if isinstance(data, dict):
        for key in ("id", "action_id", "invoice_id", "event_id", "draft_id", "user_id", "alert_id", "campaign_id"):
            if data.get(key):
                return str(data[key])
    return None
