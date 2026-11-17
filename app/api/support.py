"""Support tickets and escalation.

Only Cepoch's support staff work the queue: the Cepoch team (technical, first layer) and Jeanne
(business). Everyone else, including a client workspace's admins, raises tickets and follows only the
tickets they raised, through :func:`freedombot.support.requester_view`.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, run_scope
from freedombot.control.models import ClientSettings
from freedombot.tenancy.context import TenantContext
from freedombot.tools.base import guarded_call
from freedombot.types import PolicyEffect
import freedombot.support as support

router = APIRouter(prefix="/api/support", tags=["support"])


def staff_team(ctx: TenantContext) -> Optional[str]:
    """CEPOCH or JEANNE when the caller works the support queue; ``None`` for everyone else."""
    team = ctx.attributes.get("support_team")
    return team if team in support.TEAMS else None


def is_support_staff(ctx: TenantContext) -> bool:
    return staff_team(ctx) is not None


def require_support_staff(ctx: TenantContext = Depends(current_context)) -> TenantContext:
    if not is_support_staff(ctx):
        raise HTTPException(status_code=403, detail="The support queue is for FreedomBot support staff only")
    return ctx


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = ""
    severity: str = "NORMAL"
    #: The chat the issue came from; its recent turns are attached on the server.
    session_id: Optional[str] = None


class FlagBody(BaseModel):
    session_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    comment: str = Field(default="", max_length=2000)


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class StatusBody(BaseModel):
    status: str
    resolution: str = Field(default="", max_length=4000)


class ReassignBody(BaseModel):
    team: str
    note: str = Field(default="", max_length=2000)


class EscalateBody(BaseModel):
    reason: str
    note: str = ""


def _client_names() -> dict[str, dict[str, str]]:
    from freedombot.control import store as control

    return {c.id: {"name": c.company_name, "slug": c.slug} for c in control.list_clients()}


def _raised_by(ticket: dict[str, Any], people: dict[str, dict[str, str]]) -> dict[str, str]:
    """Who raised the ticket: from its context, else the account behind ``created_by`` (older tickets)."""
    user = (ticket.get("context") or {}).get("user") or {}
    if user.get("email"):
        return {"name": user.get("name") or "", "email": user["email"]}
    created_by = str(ticket.get("created_by") or "")
    if created_by not in people:
        from freedombot.control import store as control

        try:
            account = control.get_user(created_by)
            people[created_by] = {"name": account.display_name or "", "email": account.email}
        except Exception:  # noqa: BLE001 - seeded or system tickets have no account
            people[created_by] = {"name": created_by or "system", "email": ""}
    return people[created_by]


def _staff_ticket(
    ticket: dict[str, Any],
    names: Optional[dict[str, dict[str, str]]] = None,
    people: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    names = names if names is not None else _client_names()
    out = {k: v for k, v in ticket.items() if k != "dedupe_key"}
    out["client"] = names.get(ticket["client_id"], {"name": ticket["client_id"], "slug": ""})
    out["team_label"] = support.TEAM_LABELS.get(ticket["assignee"], ticket["assignee"])
    out["raised_by"] = _raised_by(ticket, people if people is not None else {})
    return out


def _raise(ctx: TenantContext, settings: ClientSettings, arguments: dict[str, Any], session_id: Optional[str]):
    """Raise through the same policy check as the assistant's support_create_ticket tool."""
    with bound(ctx):
        outcome = guarded_call(run_scope(ctx, settings, session_id=session_id), "support_create_ticket", arguments)
    if not outcome.ok:
        code = 403 if outcome.effect == PolicyEffect.DENY else 400
        raise HTTPException(status_code=code, detail=outcome.message or "The ticket was not raised.")
    ticket = support.get_ticket(outcome.data["id"])
    merged = bool(outcome.data.get("merged"))
    return {
        "ticket": _staff_ticket(ticket) if is_support_staff(ctx) else support.requester_view(ticket),
        "merged": merged,
        "message": (
            f"This matches open ticket {ticket['id']}, so your details were added to it."
            if merged
            else f"Ticket {ticket['id']} opened with FreedomBot support."
        ),
    }


@router.get("/me")
def support_me(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    team = staff_team(ctx)
    return {"is_staff": team is not None, "team": team, "team_label": support.TEAM_LABELS.get(team or "")}


@router.get("/tickets")
def list_support_tickets(
    view: str = Query(default="team"),
    status: Optional[str] = Query(default=None),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """Staff: their team's queue (default), Cepoch triage, or the complete history. Everyone else: the
    tickets they raised, in this workspace."""
    team = staff_team(ctx)
    if team is None:
        items = support.list_tickets(client_id=ctx.client_id, created_by=ctx.user_id, status=status)
        return {"items": [support.requester_view(t) for t in items], "can_manage": False, "team": None}
    if view == "all":
        items = support.list_tickets(status=status)
    elif view == "triage":
        items = support.list_tickets(triage=True, status=status)
    else:
        items = support.list_tickets(assignee=team, status=status)
    names, people = _client_names(), {}
    return {"items": [_staff_ticket(t, names, people) for t in items], "can_manage": True, "team": team,
            "team_label": support.TEAM_LABELS[team]}


@router.post("/tickets")
def create_support_ticket(
    body: TicketCreate,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """The Escalate form: anyone signed in may raise a ticket, as they can from the chat."""
    arguments = {"subject": body.subject, "body": body.body, "severity": body.severity, "source": "MANUAL"}
    return _raise(ctx, settings, arguments, body.session_id)


@router.post("/flag")
def flag_answer(
    body: FlagBody,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Report one assistant answer. The question, the answer and the chat around it are attached from
    the caller's own saved conversation; flagging the same answer again does not open a second ticket."""
    arguments = {"source": "AI_FLAG", "message_id": body.message_id, "body": body.comment}
    return _raise(ctx, settings, arguments, body.session_id)


def _staff_get(ticket_id: str) -> dict[str, Any]:
    try:
        return support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc


@router.get("/tickets/{ticket_id}")
def get_support_ticket(ticket_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Staff see the whole ticket. The person who raised it sees its status; nobody else sees it at all."""
    try:
        ticket = support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    if is_support_staff(ctx):
        return {"ticket": _staff_ticket(ticket)}
    if ticket["client_id"] != ctx.client_id or ticket.get("created_by") != ctx.user_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket": support.requester_view(ticket)}


@router.post("/tickets/{ticket_id}/notes")
def add_ticket_note(ticket_id: str, body: NoteBody, ctx: TenantContext = Depends(require_support_staff)) -> dict[str, Any]:
    _staff_get(ticket_id)
    return {"ticket": _staff_ticket(support.add_note(ticket_id=ticket_id, note=body.note, by=_who(ctx)))}


@router.post("/tickets/{ticket_id}/status")
def set_ticket_status(
    ticket_id: str, body: StatusBody, ctx: TenantContext = Depends(require_support_staff)
) -> dict[str, Any]:
    ticket = _staff_get(ticket_id)
    if body.status.upper() == support.STATUS_RESOLVED and not (body.resolution.strip() or ticket["resolution"]):
        raise HTTPException(status_code=400, detail="Say how it was resolved: the client sees the resolution.")
    try:
        updated = support.set_status(ticket_id=ticket_id, status=body.status, by=_who(ctx), resolution=body.resolution)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ticket": _staff_ticket(updated)}


@router.post("/tickets/{ticket_id}/reassign")
def reassign_ticket(
    ticket_id: str, body: ReassignBody, ctx: TenantContext = Depends(require_support_staff)
) -> dict[str, Any]:
    """Hand the same ticket to the other team, with its notes, context and history."""
    _staff_get(ticket_id)
    try:
        updated = support.reassign(ticket_id=ticket_id, team=body.team, by=_who(ctx), note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ticket": _staff_ticket(updated)}


@router.post("/tickets/{ticket_id}/escalate")
def escalate_ticket(
    ticket_id: str, body: EscalateBody, ctx: TenantContext = Depends(require_support_staff)
) -> dict[str, Any]:
    _staff_get(ticket_id)
    try:
        updated = support.escalate_to_jeanne(ticket_id=ticket_id, reason=body.reason, by=_who(ctx), note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ticket": _staff_ticket(updated)}


@router.post("/tickets/{ticket_id}/return-to-cepoch")
def return_ticket(ticket_id: str, body: NoteBody, ctx: TenantContext = Depends(require_support_staff)) -> dict[str, Any]:
    _staff_get(ticket_id)
    return {"ticket": _staff_ticket(support.return_to_cepoch(ticket_id=ticket_id, by=_who(ctx), note=body.note))}


@router.get("/notifications")
def list_notifications(ctx: TenantContext = Depends(require_support_staff)) -> dict[str, Any]:
    """New and reassigned tickets for the caller's team."""
    return support.notifications(staff_team(ctx) or "")


@router.post("/notifications/read")
def read_notifications(ctx: TenantContext = Depends(require_support_staff)) -> dict[str, Any]:
    support.mark_notifications_read(staff_team(ctx) or "")
    return {"ok": True}


def _who(ctx: TenantContext) -> str:
    """How an action shows in a ticket's history: the person's email, else their user id."""
    return ctx.email or ctx.user_id
