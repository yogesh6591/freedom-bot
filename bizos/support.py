"""
Post-sale support (FB-046)
==========================

Routine tickets stay with Cepoch. Jeanne is pulled only for CEO-level judgment,
relationship risk, or at-risk accounts. Support is not a sales desk.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text

from bizos.control.db import control_connection
from bizos.util.ids import new_id
from bizos.util.timeutil import utcnow

ASSIGNEE_CEPOCH = "CEPOCH"
ASSIGNEE_JEANNE = "JEANNE"

ESCALATE_CEO = "CEO_JUDGMENT"
ESCALATE_RELATIONSHIP = "RELATIONSHIP_RISK"
ESCALATE_AT_RISK = "AT_RISK"
ESCALATE_REASONS = frozenset({ESCALATE_CEO, ESCALATE_RELATIONSHIP, ESCALATE_AT_RISK})

STATUS_OPEN = "OPEN"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_WAITING = "WAITING"
STATUS_RESOLVED = "RESOLVED"
STATUS_ESCALATED = "ESCALATED"

SEVERITY_NORMAL = "NORMAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"


def _history(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _row_to_ticket(row: Any) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "client_id": data["client_id"],
        "subject": data["subject"],
        "body": data.get("body") or "",
        "severity": data["severity"],
        "status": data["status"],
        "assignee": data["assignee"],
        "escalate_reason": data.get("escalate_reason"),
        "created_by": data.get("created_by"),
        "created_at": data["created_at"].isoformat() if data.get("created_at") else None,
        "updated_at": data["updated_at"].isoformat() if data.get("updated_at") else None,
        "history": _history(data.get("history")),
    }


def create_ticket(
    *,
    client_id: str,
    subject: str,
    body: str = "",
    severity: str = SEVERITY_NORMAL,
    created_by: str = "system",
) -> dict[str, Any]:
    """Open a ticket assigned to Cepoch by default (never Jeanne)."""
    sev = (severity or SEVERITY_NORMAL).upper()
    if sev not in {SEVERITY_NORMAL, SEVERITY_HIGH, SEVERITY_CRITICAL}:
        sev = SEVERITY_NORMAL
    ticket_id = new_id("sup")
    now = utcnow()
    history = [
        {
            "at": now.isoformat(),
            "by": created_by,
            "event": "created",
            "note": "Routed to Cepoch (routine support).",
        }
    ]
    with control_connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO support_tickets (
                    id, client_id, subject, body, severity, status, assignee,
                    escalate_reason, created_by, created_at, updated_at, history
                ) VALUES (
                    :id, :client_id, :subject, :body, :severity, :status, :assignee,
                    NULL, :created_by, :now, :now, CAST(:history AS jsonb)
                )
                """
            ),
            {
                "id": ticket_id,
                "client_id": client_id,
                "subject": subject.strip()[:300],
                "body": (body or "")[:8000],
                "severity": sev,
                "status": STATUS_OPEN,
                "assignee": ASSIGNEE_CEPOCH,
                "created_by": created_by,
                "now": now,
                "history": json.dumps(history),
            },
        )
    return get_ticket(ticket_id)


def get_ticket(ticket_id: str) -> dict[str, Any]:
    with control_connection() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM support_tickets WHERE id = :id"),
                {"id": ticket_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise LookupError(ticket_id)
    return _row_to_ticket(row)


def list_tickets(
    *,
    client_id: Optional[str] = None,
    assignee: Optional[str] = None,
    status: Optional[str] = None,
    needs_jeanne: bool = False,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if client_id:
        clauses.append("client_id = :client_id")
        params["client_id"] = client_id
    if assignee:
        clauses.append("assignee = :assignee")
        params["assignee"] = assignee.upper()
    if status:
        clauses.append("status = :status")
        params["status"] = status.upper()
    if needs_jeanne:
        clauses.append("assignee = :jeanne")
        params["jeanne"] = ASSIGNEE_JEANNE
        clauses.append("status <> :resolved")
        params["resolved"] = STATUS_RESOLVED
    sql = (
        "SELECT * FROM support_tickets WHERE "
        + " AND ".join(clauses)
        + " ORDER BY updated_at DESC"
    )
    with control_connection() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [_row_to_ticket(r) for r in rows]


def add_note(*, ticket_id: str, note: str, by: str) -> dict[str, Any]:
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"] or [])
    history.append(
        {
            "at": utcnow().isoformat(),
            "by": by,
            "event": "note",
            "note": (note or "").strip()[:2000],
        }
    )
    return _update(ticket_id, history=history)


def set_status(*, ticket_id: str, status: str, by: str) -> dict[str, Any]:
    st = (status or "").upper()
    allowed = {
        STATUS_OPEN,
        STATUS_IN_PROGRESS,
        STATUS_WAITING,
        STATUS_RESOLVED,
        STATUS_ESCALATED,
    }
    if st not in allowed:
        raise ValueError(f"unknown status: {status}")
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"] or [])
    history.append(
        {
            "at": utcnow().isoformat(),
            "by": by,
            "event": "status",
            "note": f"Status → {st}",
        }
    )
    fields: dict[str, Any] = {"status": st, "history": history}
    if st == STATUS_RESOLVED:
        fields["assignee"] = ASSIGNEE_CEPOCH
        fields["escalate_reason"] = None
        history.append(
            {
                "at": utcnow().isoformat(),
                "by": by,
                "event": "routed",
                "note": "Resolved — ownership back to Cepoch.",
            }
        )
        fields["history"] = history
    return _update(ticket_id, **fields)


def escalate_to_jeanne(
    *,
    ticket_id: str,
    reason: str,
    by: str,
    note: str = "",
) -> dict[str, Any]:
    """Pull Jeanne only for allowed exception reasons (FB-046)."""
    why = (reason or "").upper()
    if why not in ESCALATE_REASONS:
        raise ValueError(
            "Jeanne escalation requires CEO_JUDGMENT, RELATIONSHIP_RISK, or AT_RISK"
        )
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"] or [])
    history.append(
        {
            "at": utcnow().isoformat(),
            "by": by,
            "event": "escalated",
            "note": note.strip()[:2000]
            or f"Escalated to Jeanne ({why}). Not a sales handoff.",
        }
    )
    return _update(
        ticket_id,
        assignee=ASSIGNEE_JEANNE,
        status=STATUS_ESCALATED,
        escalate_reason=why,
        history=history,
    )


def return_to_cepoch(*, ticket_id: str, by: str, note: str = "") -> dict[str, Any]:
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"] or [])
    history.append(
        {
            "at": utcnow().isoformat(),
            "by": by,
            "event": "returned",
            "note": note.strip()[:2000] or "Returned to Cepoch support.",
        }
    )
    return _update(
        ticket_id,
        assignee=ASSIGNEE_CEPOCH,
        status=STATUS_IN_PROGRESS,
        escalate_reason=None,
        history=history,
    )


def _update(ticket_id: str, **fields: Any) -> dict[str, Any]:
    sets = ["updated_at = :now"]
    params: dict[str, Any] = {"id": ticket_id, "now": utcnow()}
    for key, value in fields.items():
        if key == "history":
            sets.append("history = CAST(:history AS jsonb)")
            params["history"] = json.dumps(value)
        elif key == "escalate_reason":
            sets.append("escalate_reason = :escalate_reason")
            params["escalate_reason"] = value
        elif key in {"status", "assignee", "severity", "subject", "body"}:
            sets.append(f"{key} = :{key}")
            params[key] = value
    with control_connection() as conn:
        conn.execute(
            text(f"UPDATE support_tickets SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
    return get_ticket(ticket_id)


def ensure_demo_tickets(*, client_id: str, actor: str = "bootstrap") -> list[dict[str, Any]]:
    """Seed 1–2 routine tickets + one Jeanne escalation for demos (idempotent)."""
    existing = list_tickets(client_id=client_id)
    if existing:
        return existing
    create_ticket(
        client_id=client_id,
        subject="Payroll SOP question — how to re-run attendance export",
        body="Client ops asked for the monthly payroll attendance steps. Routine how-to.",
        severity=SEVERITY_NORMAL,
        created_by=actor,
    )
    b = create_ticket(
        client_id=client_id,
        subject="Invoice draft stuck in approvals queue",
        body="Fiona cannot see why INV draft is waiting. Cepoch can clear.",
        severity=SEVERITY_HIGH,
        created_by=actor,
    )
    set_status(ticket_id=b["id"], status=STATUS_IN_PROGRESS, by=actor)
    c = create_ticket(
        client_id=client_id,
        subject="CEO wants FreedomBot to decide legal risk on MSA",
        body="Client CEO asked for a licensed legal determination. Exception path only.",
        severity=SEVERITY_CRITICAL,
        created_by=actor,
    )
    escalate_to_jeanne(
        ticket_id=c["id"],
        reason=ESCALATE_CEO,
        by=actor,
        note="CEO-level judgment — pull Jeanne; not routine support.",
    )
    return list_tickets(client_id=client_id)
