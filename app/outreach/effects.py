"""Outreach and acquisition tools as ops-style effects.

Each tool is registered in freedombot/rbac/registry.py (policy), freedombot/agents/tool_meta.py (what the model reads) and
freedombot/agents/tool_args.py (argument shapes). The effects below only run after ``guarded_call`` allowed the call.
The chat turn's OutreachSession is ``scope.campaign_session`` (field renamed in a later cleanup).

Two kinds of tool:
* email tools           methods of ``OutreachSession`` (recipients, draft, send)
* acquisition tools     functions registered in ``app.outreach.toolkit`` (records, assessments, bookings, ...)
"""

from __future__ import annotations

from typing import Any

from app.outreach import toolkit
from freedombot.connectors.base import ConnectorResult
from freedombot.tools.base import RunScope, effect

#: email tool name -> OutreachSession method and the arguments it accepts
_EMAIL_METHODS: dict[str, tuple[str, tuple[str, ...]]] = {
    "choose_recipients": ("choose_recipients", ("people", "mode")),
    "propose_email": ("propose_email", ("subject", "body", "change_evidence", "per_person", "new_thread", "same_for_all")),
    "set_person_details": ("set_person_details", ("email", "first_name", "last_name", "company", "evidence")),
    "send_approved": ("send_approved", ("approval_evidence",)),
    "send_from_template": ("send_from_template", ("people", "audience", "stage", "order", "count", "emailed",
                                                   "inactive_days", "text", "place", "include_recent",
                                                   "subject", "body", "approval_evidence")),
    "find_people": ("find_people", ("stage", "order", "count", "emailed", "inactive_days", "text", "place",
                                    "include_recent")),
    "show_email_template": ("show_email_template", ()),
    "update_email_template": ("update_email_template",
                              ("first_subject", "first_body", "stage", "stage_body", "change_evidence", "remove_placeholders")),
    "show_followup_schedule": ("show_followup_schedule", ()),
    "update_followup_schedule": ("update_followup_schedule",
                                  ("enabled", "days", "hour", "max_followups", "change_evidence")),
    "cancel_sending": ("cancel_sending", ()),
}

toolkit.load_all()
#: every tool this module serves
TOOL_NAMES: tuple[str, ...] = (*_EMAIL_METHODS, *toolkit.TOOLS)


def _result(tool: str, text: str) -> ConnectorResult:
    if str(text).startswith("error:"):
        return ConnectorResult.failure(str(text)[6:].strip())
    # The labelled assessment report is the user-facing reply; keep the full text on data.
    data = str(text) if tool == "show_assessment" else None
    return ConnectorResult(ok=True, summary=str(text), data=data)


def _run(scope: RunScope, tool: str, args: dict[str, Any]) -> ConnectorResult:
    session = scope.campaign_session
    if session is None:
        return ConnectorResult.failure("Outreach is not available in this conversation.")
    client_id = getattr(session.ctx, "client_id", None) or ""
    if not client_id:
        return ConnectorResult.failure("This workspace could not be identified for prospect access.")
    from acquisition.tenant import tenant_scope

    with tenant_scope(client_id):
        if tool in toolkit.TOOLS:
            text = toolkit.run(
                tool, db_factory=session.db_factory, state=session.state, latest=session.latest,
                user_id=getattr(session.ctx, "user_id", None), client_id=client_id, args=args,
            )
            if session.state.pop("_show_card", False):
                session.card_shown = True  # a tool put an email on the card (the payment link)
            return _result(tool, text)
        method, allowed = _EMAIL_METHODS[tool]
        kwargs = {k: args[k] for k in allowed if k in args and args[k] is not None}
        try:
            text = getattr(session, method)(**kwargs)
        except Exception as exc:  # noqa: BLE001 - a bug in one tool must not break the chat turn
            return ConnectorResult.failure(f"{tool} failed: {exc}")
        return _result(tool, text)


def _register(tool: str) -> None:
    @effect(tool)
    def _effect(scope: RunScope, args: dict[str, Any], _tool: str = tool) -> ConnectorResult:
        return _run(scope, _tool, args)


for _name in TOOL_NAMES:
    _register(_name)
