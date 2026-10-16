"""
Support Tool Effects (FB-046)
=============================

Raise and check Cepoch support tickets from chat. Tickets always start with
Cepoch; only support staff can pull Jeanne in, so the assistant never can.
"""

from __future__ import annotations

from typing import Any

from bizos import support
from bizos.connectors.base import ConnectorResult
from bizos.tools.base import RunScope, effect


def _render(ticket: dict[str, Any]) -> dict[str, Any]:
    history = ticket.get("history") or []
    return {
        "id": ticket["id"],
        "subject": ticket["subject"],
        "severity": ticket["severity"],
        "status": ticket["status"],
        "handled_by": "Cepoch support",
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "latest_update": (history[-1].get("note") if history else "") or "",
    }


@effect("support_create_ticket")
def support_create_ticket(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    subject = str(args.get("subject") or "").strip()
    if not subject:
        return ConnectorResult.failure("subject is required — summarize the problem in one line.")
    body = str(args.get("body") or "").strip()
    if scope.session_id:
        body = f"{body}\n\nRaised from FreedomBot chat (session {scope.session_id}).".strip()
    ticket, merged = support.submit_ticket(
        client_id=scope.ctx.client_id,
        subject=subject,
        body=body,
        severity=str(args.get("severity") or support.SEVERITY_NORMAL),
        created_by=scope.ctx.user_id,
    )
    return ConnectorResult(
        ok=True,
        data=_render(ticket),
        summary=(
            f"added to the open Cepoch support ticket {ticket['id']} with the same subject"
            if merged
            else f"opened Cepoch support ticket {ticket['id']} ({ticket['severity']})"
        ),
    )


@effect("support_my_tickets")
def support_my_tickets(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    mine = [
        t for t in support.list_tickets(client_id=scope.ctx.client_id)
        if t.get("created_by") == scope.ctx.user_id
    ]
    if not mine:
        return ConnectorResult(ok=True, data=[], summary="this user has not raised any support tickets")
    return ConnectorResult(
        ok=True,
        data=[_render(t) for t in mine[:20]],
        summary=f"{len(mine)} support ticket(s) raised by this user",
    )
