"""
Support Tickets and Escalation
==============================

Tickets reach only Cepoch's two support teams, never a client workspace's admins:

* **Cepoch** (``CEPOCH``) is the first support layer and takes every technical issue, plus anything
  the router is unsure of (the triage queue).
* **Jeanne** (``JEANNE``) takes business and operational issues.

Routing is :mod:`freedombot.support_routing`. Either team can hand a ticket to the other; the ticket,
its notes, its context and its whole history stay with it. Each routing or hand-over leaves an in-app
notice for the team that receives it.

A ticket comes from one of three places (``source``): a flagged AI response, the Escalate form, or
the assistant in chat. Repeats are merged instead of opening a second ticket (:func:`submit_ticket`).

The person who raised a ticket sees only :func:`requester_view`: its status and, once it is done,
the resolution. Never the team, the routing reason, internal notes or history.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text

from freedombot.control.db import control_connection
from freedombot.support_routing import (
    BUSINESS,
    SOURCE_AI_FLAG,
    SOURCE_CHAT,
    SOURCE_MANUAL,
    SOURCES,
    TECHNICAL,
    classify,
)
from freedombot.util.ids import new_id
from freedombot.util.timeutil import utcnow

ASSIGNEE_CEPOCH = "CEPOCH"
ASSIGNEE_JEANNE = "JEANNE"
TEAMS = (ASSIGNEE_CEPOCH, ASSIGNEE_JEANNE)
TEAM_LABELS = {ASSIGNEE_CEPOCH: "Cepoch support", ASSIGNEE_JEANNE: "Jeanne"}
#: The person each team's tickets go to.
TEAM_EMAILS = {ASSIGNEE_CEPOCH: "yogesh@cepoch.com", ASSIGNEE_JEANNE: "jeanne@cepoch.com"}

ESCALATE_CEO = "CEO_JUDGMENT"
ESCALATE_RELATIONSHIP = "RELATIONSHIP_RISK"
ESCALATE_AT_RISK = "AT_RISK"
ESCALATE_REASONS = frozenset({ESCALATE_CEO, ESCALATE_RELATIONSHIP, ESCALATE_AT_RISK})

STATUS_OPEN = "OPEN"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_RESOLVED = "RESOLVED"
STATUS_CLOSED = "CLOSED"
#: Older statuses still read correctly; new changes use the four above.
STATUS_WAITING = "WAITING"
STATUS_ESCALATED = "ESCALATED"
STATUSES = (STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_CLOSED)
DONE = frozenset({STATUS_RESOLVED, STATUS_CLOSED})

SEVERITY_NORMAL = "NORMAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"
SEVERITIES = (SEVERITY_NORMAL, SEVERITY_HIGH, SEVERITY_CRITICAL)

#: M14-18: a resubmission of an open ticket within this window is merged.
DUPLICATE_WINDOW_HOURS = 72


#: Cepoch's internal workspace, where the support accounts sign in.
SUPPORT_WORKSPACE_SLUG = "cepoch"


def team_of(user: Any, client_slug: str) -> Optional[str]:
    """The support team a person works on: the one set on their account, else the team their address is
    named for, when they sign in to Cepoch's internal workspace. Takes effect at once, without a restart."""
    team = getattr(user, "support_team", None)
    if team in TEAMS:
        return team
    if client_slug != SUPPORT_WORKSPACE_SLUG:
        return None
    email = str(getattr(user, "email", "") or "").strip().casefold()
    return next((t for t, address in TEAM_EMAILS.items() if address == email), None)


def is_done(status: str) -> bool:
    return str(status or "").upper() in DONE


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


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
        "assigned_to": TEAM_EMAILS.get(data["assignee"]),
        "category": data.get("category") or TECHNICAL,
        "source": data.get("source") or SOURCE_MANUAL,
        "triage": bool(data.get("triage")),
        "context": _json(data.get("context"), {}),
        "resolution": data.get("resolution") or "",
        "escalate_reason": data.get("escalate_reason"),
        "created_by": data.get("created_by"),
        "created_at": data["created_at"].isoformat() if data.get("created_at") else None,
        "updated_at": data["updated_at"].isoformat() if data.get("updated_at") else None,
        "history": list(_json(data.get("history"), [])),
    }


def requester_view(ticket: dict[str, Any]) -> dict[str, Any]:
    """What the person who raised a ticket sees: its status and, once done, the resolution.

    Never the team it went to, the routing reason, internal notes, history or context.
    """
    done = is_done(ticket["status"])
    return {
        "id": ticket["id"],
        "subject": ticket["subject"],
        "severity": ticket["severity"],
        "status": ticket["status"] if ticket["status"] in STATUSES else STATUS_IN_PROGRESS,
        "handled_by": "FreedomBot support",
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "resolution": ticket.get("resolution", "") if done else "",
    }


def _event(by: str, event: str, note: str, **extra: Any) -> dict[str, Any]:
    return {"at": utcnow().isoformat(), "by": by, "event": event, "note": (note or "").strip()[:2000], **extra}


# --------------------------------------------------------------------------- raising


def _normalize_subject(subject: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (subject or "").casefold()).split())


def find_open_duplicate(
    *, client_id: str, subject: str, dedupe_key: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """An unfinished ticket in this client for the same thing: the same flagged message, or the same
    subject raised in the last few days."""
    wanted = _normalize_subject(subject)
    cutoff = utcnow() - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    for ticket in list_tickets(client_id=client_id):
        if is_done(ticket["status"]):
            continue
        if dedupe_key and ticket.get("dedupe_key") == dedupe_key:
            return ticket
        created = ticket.get("created_at")
        if created and datetime.fromisoformat(created) < cutoff:
            continue
        if wanted and _normalize_subject(ticket["subject"]) == wanted:
            return ticket
    return None


def submit_ticket(
    *,
    client_id: str,
    subject: str,
    body: str = "",
    severity: str = SEVERITY_NORMAL,
    created_by: str = "system",
    source: str = SOURCE_MANUAL,
    context: Optional[dict[str, Any]] = None,
    category_hint: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> tuple[dict[str, Any], bool]:
    """Open a ticket, or add to the open one it repeats (M14-18). Returns ``(ticket, merged)``.

    The same flagged message (``dedupe_key``) or the same subject while still open is one ticket: a
    retry or a repeated message never opens a second.
    """
    existing = find_open_duplicate(client_id=client_id, subject=subject, dedupe_key=dedupe_key)
    if existing is None:
        return create_ticket(
            client_id=client_id, subject=subject, body=body, severity=severity, created_by=created_by,
            source=source, context=context, category_hint=category_hint, dedupe_key=dedupe_key,
        ), False
    if dedupe_key and existing.get("dedupe_key") == dedupe_key and not (body or "").strip():
        return existing, True
    history = list(existing["history"])
    history.append(_event(created_by, "duplicate_merged", "Same issue submitted again. " + (body or "").strip()))
    return _update(existing["id"], history=history), True


def create_ticket(
    *,
    client_id: str,
    subject: str,
    body: str = "",
    severity: str = SEVERITY_NORMAL,
    created_by: str = "system",
    source: str = SOURCE_MANUAL,
    context: Optional[dict[str, Any]] = None,
    category_hint: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> dict[str, Any]:
    """Open a ticket and route it: technical to Cepoch, business to Jeanne, unclear to Cepoch triage."""
    sev = (severity or SEVERITY_NORMAL).upper()
    sev = sev if sev in SEVERITIES else SEVERITY_NORMAL
    src = source if source in SOURCES else SOURCE_MANUAL
    routing = classify(subject, body, source=src, hint=category_hint)
    team = routing.team
    ticket_id = new_id("sup")
    where = "Cepoch triage" if routing.triage else TEAM_LABELS[team]
    history = [_event(created_by, "created", f"Routed to {where}: {routing.reason}.", team=team)]
    now = utcnow()
    with control_connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO support_tickets (
                    id, client_id, subject, body, severity, status, assignee, escalate_reason,
                    created_by, created_at, updated_at, history, category, source, triage, context, dedupe_key
                ) VALUES (
                    :id, :client_id, :subject, :body, :severity, :status, :assignee, NULL,
                    :created_by, :now, :now, CAST(:history AS jsonb), :category, :source, :triage,
                    CAST(:context AS jsonb), :dedupe_key
                )
                """
            ),
            {
                "id": ticket_id, "client_id": client_id, "subject": subject.strip()[:300],
                "body": (body or "")[:8000], "severity": sev, "status": STATUS_OPEN, "assignee": team,
                "created_by": created_by, "now": now, "history": json.dumps(history),
                "category": routing.category, "source": src, "triage": routing.triage,
                "context": json.dumps(context or {}, default=str), "dedupe_key": dedupe_key,
            },
        )
    ticket = get_ticket(ticket_id)
    notify(ticket, team, "new", f"New {'triage ' if routing.triage else ''}ticket: {ticket['subject']}")
    from freedombot.support_email import notify_new_ticket

    notify_new_ticket(ticket, team)
    return ticket


def get_ticket(ticket_id: str) -> dict[str, Any]:
    with control_connection() as conn:
        row = conn.execute(text("SELECT * FROM support_tickets WHERE id = :id"), {"id": ticket_id}).mappings().first()
    if row is None:
        raise LookupError(ticket_id)
    ticket = _row_to_ticket(row)
    ticket["dedupe_key"] = dict(row).get("dedupe_key")
    return ticket


def list_tickets(
    *,
    client_id: Optional[str] = None,
    assignee: Optional[str] = None,
    status: Optional[str] = None,
    needs_jeanne: bool = False,
    triage: Optional[bool] = None,
    created_by: Optional[str] = None,
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
    if triage is not None:
        clauses.append("triage = :triage")
        params["triage"] = triage
    if created_by:
        clauses.append("created_by = :created_by")
        params["created_by"] = created_by
    if needs_jeanne:
        clauses.append("assignee = :jeanne AND status NOT IN ('RESOLVED', 'CLOSED')")
        params["jeanne"] = ASSIGNEE_JEANNE
    sql = "SELECT * FROM support_tickets WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC"
    with control_connection() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out = []
    for row in rows:
        ticket = _row_to_ticket(row)
        ticket["dedupe_key"] = dict(row).get("dedupe_key")
        out.append(ticket)
    return out


# --------------------------------------------------------------------------- working a ticket


def add_note(*, ticket_id: str, note: str, by: str) -> dict[str, Any]:
    """An internal note. Support staff see it; the person who raised the ticket never does."""
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"])
    history.append(_event(by, "note", note, internal=True))
    return _update(ticket_id, history=history)


def set_status(*, ticket_id: str, status: str, by: str, resolution: str = "") -> dict[str, Any]:
    """Move a ticket to Open, In progress, Resolved or Closed. The team that owns it keeps it."""
    st = (status or "").upper()
    if st not in STATUSES:
        raise ValueError(f"unknown status: {status}. Use one of: {', '.join(STATUSES)}")
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"])
    history.append(_event(by, "status", f"Status → {st}"))
    fields: dict[str, Any] = {"status": st, "history": history}
    if resolution.strip():
        fields["resolution"] = resolution.strip()[:4000]
        history.append(_event(by, "resolution", resolution))
    return _update(ticket_id, **fields)


def reassign(*, ticket_id: str, team: str, by: str, note: str = "") -> dict[str, Any]:
    """Hand a ticket to the other team. Same ticket: its notes, context and history stay with it."""
    target = (team or "").upper()
    if target not in TEAMS:
        raise ValueError(f"team must be one of: {', '.join(TEAMS)}")
    ticket = get_ticket(ticket_id)
    if ticket["assignee"] == target and not ticket["triage"]:
        raise ValueError(f"This ticket is already with {TEAM_LABELS[target]}.")
    history = list(ticket["history"])
    history.append(_event(by, "reassigned", note or f"Reassigned to {TEAM_LABELS[target]}.",
                          from_team=ticket["assignee"], team=target))
    category = BUSINESS if target == ASSIGNEE_JEANNE else TECHNICAL
    status = STATUS_OPEN if is_done(ticket["status"]) else ticket["status"]
    if status not in STATUSES:
        status = STATUS_IN_PROGRESS
    updated = _update(ticket_id, assignee=target, category=category, triage=False, status=status,
                      escalate_reason=None, history=history)
    notify(updated, target, "reassigned", f"Reassigned to you: {updated['subject']}")
    return updated


def escalate_to_jeanne(*, ticket_id: str, reason: str, by: str, note: str = "") -> dict[str, Any]:
    """Pull Jeanne in for an exception (FB-046: CEO judgment, relationship risk, at-risk account)."""
    why = (reason or "").upper()
    if why not in ESCALATE_REASONS:
        raise ValueError("Jeanne escalation requires CEO_JUDGMENT, RELATIONSHIP_RISK, or AT_RISK")
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"])
    history.append(_event(by, "escalated", note or f"Escalated to Jeanne ({why}). Not a sales handoff.",
                          from_team=ticket["assignee"], team=ASSIGNEE_JEANNE))
    updated = _update(ticket_id, assignee=ASSIGNEE_JEANNE, status=STATUS_ESCALATED, escalate_reason=why,
                      triage=False, history=history)
    notify(updated, ASSIGNEE_JEANNE, "escalated", f"Escalated to you: {updated['subject']}")
    return updated


def return_to_cepoch(*, ticket_id: str, by: str, note: str = "") -> dict[str, Any]:
    ticket = get_ticket(ticket_id)
    history = list(ticket["history"])
    history.append(_event(by, "returned", note or "Returned to Cepoch support.",
                          from_team=ticket["assignee"], team=ASSIGNEE_CEPOCH))
    updated = _update(ticket_id, assignee=ASSIGNEE_CEPOCH, status=STATUS_IN_PROGRESS, escalate_reason=None,
                      history=history)
    notify(updated, ASSIGNEE_CEPOCH, "reassigned", f"Returned to you: {updated['subject']}")
    return updated


_COLUMNS = {"status", "assignee", "severity", "subject", "body", "category", "triage", "resolution", "escalate_reason"}


def _update(ticket_id: str, **fields: Any) -> dict[str, Any]:
    sets = ["updated_at = :now"]
    params: dict[str, Any] = {"id": ticket_id, "now": utcnow()}
    for key, value in fields.items():
        if key == "history":
            sets.append("history = CAST(:history AS jsonb)")
            params["history"] = json.dumps(value)
        elif key in _COLUMNS:
            sets.append(f"{key} = :{key}")
            params[key] = value
    with control_connection() as conn:
        conn.execute(text(f"UPDATE support_tickets SET {', '.join(sets)} WHERE id = :id"), params)
    return get_ticket(ticket_id)


# --------------------------------------------------------------------------- notifications


def notify(ticket: dict[str, Any], team: str, kind: str, message: str) -> None:
    """Leave an in-app notice for the team that now has the ticket. Only Cepoch and Jeanne get these."""
    if team not in TEAMS:
        return
    with control_connection() as conn:
        conn.execute(
            text("INSERT INTO support_notifications (id, ticket_id, team, kind, message) VALUES (:i, :t, :team, :k, :m)"),
            {"i": new_id("snt"), "t": ticket["id"], "team": team, "k": kind, "m": message[:500]},
        )


def notifications(team: str, *, limit: int = 30) -> dict[str, Any]:
    with control_connection() as conn:
        rows = conn.execute(
            text("SELECT id, ticket_id, kind, message, created_at, read_at FROM support_notifications "
                 "WHERE team = :t ORDER BY created_at DESC LIMIT :n"),
            {"t": team, "n": limit},
        ).mappings().all()
        unread = conn.execute(
            text("SELECT count(*) FROM support_notifications WHERE team = :t AND read_at IS NULL"), {"t": team}
        ).scalar()
    items = [
        {**dict(r), "created_at": r["created_at"].isoformat(), "read_at": r["read_at"].isoformat() if r["read_at"] else None}
        for r in rows
    ]
    return {"unread": int(unread or 0), "items": items}


def mark_notifications_read(team: str) -> None:
    with control_connection() as conn:
        conn.execute(
            text("UPDATE support_notifications SET read_at = NOW() WHERE team = :t AND read_at IS NULL"), {"t": team}
        )


# --------------------------------------------------------------------------- a client's service record


def service_records(client_id: str) -> dict[str, Any]:
    """A client's support history for a meeting: open and resolved issues, hand-overs, resolutions."""
    tickets = list_tickets(client_id=client_id)

    def brief(t: dict[str, Any]) -> dict[str, Any]:
        notes = [h for h in t["history"] if h.get("event") == "note"]
        return {
            "id": t["id"], "subject": t["subject"], "status": t["status"], "priority": t["severity"],
            "category": t["category"], "with": TEAM_LABELS.get(t["assignee"], t["assignee"]),
            "source": t["source"], "opened": t["created_at"], "updated": t["updated_at"],
            "resolution": t["resolution"], "latest_note": notes[-1]["note"] if notes else "",
        }

    open_items = [brief(t) for t in tickets if not is_done(t["status"])]
    done_items = [brief(t) for t in tickets if is_done(t["status"])]
    handovers = [
        {"ticket": t["id"], "subject": t["subject"], "at": h.get("at"),
         "from": TEAM_LABELS.get(h.get("from_team", ""), h.get("from_team")),
         "to": TEAM_LABELS.get(h.get("team", ""), h.get("team")), "note": h.get("note", "")}
        for t in tickets for h in t["history"] if h.get("event") in {"reassigned", "escalated", "returned"}
    ]
    return {
        "total": len(tickets),
        "open": open_items,
        "outstanding_critical": [t for t in open_items if t["priority"] in (SEVERITY_HIGH, SEVERITY_CRITICAL)],
        "resolved": done_items[:20],
        "escalations": handovers[:20],
    }


# --------------------------------------------------------------------------- demo data


def ensure_demo_tickets(*, client_id: str, actor: str = "bootstrap") -> list[dict[str, Any]]:
    """Seed a technical ticket, a business ticket and an escalation for demos (idempotent)."""
    existing = list_tickets(client_id=client_id)
    if existing:
        return existing
    create_ticket(
        client_id=client_id,
        subject="Gmail sync error on the integrations page",
        body="Client ops sees a sync error after reconnecting Gmail.",
        severity=SEVERITY_NORMAL,
        created_by=actor,
    )
    b = create_ticket(
        client_id=client_id,
        subject="Subscription renewal and invoice question",
        body="Finance wants to change the billing plan at renewal. Needs business approval.",
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
        ticket_id=c["id"], reason=ESCALATE_CEO, by=actor,
        note="CEO-level judgment — pull Jeanne; not routine support.",
    )
    return list_tickets(client_id=client_id)


__all__ = [
    "ASSIGNEE_CEPOCH", "ASSIGNEE_JEANNE", "STATUS_OPEN", "STATUS_IN_PROGRESS", "STATUS_RESOLVED", "STATUS_CLOSED",
    "SOURCE_AI_FLAG", "SOURCE_CHAT", "SOURCE_MANUAL", "submit_ticket", "create_ticket", "get_ticket", "list_tickets",
    "add_note", "set_status", "reassign", "escalate_to_jeanne", "return_to_cepoch", "requester_view",
    "notifications", "mark_notifications_read", "service_records",
]
