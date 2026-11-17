"""FB-046 — Cepoch support queue (routine tickets; Jeanne only on escalate)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, require_admin, run_scope
from freedombot.control.models import ClientSettings
from freedombot.tenancy.context import TenantContext
from freedombot.tools.base import guarded_call
from freedombot.types import PolicyEffect, Role
import freedombot.support as support

router = APIRouter(prefix="/api/support", tags=["support"])


def is_support_staff(ctx: TenantContext) -> bool:
    """Cepoch support works the queue: ADMIN or OPERATOR of the workspace."""
    return ctx.has_role(Role.ADMIN) or ctx.has_role(Role.OPERATOR)


def require_support_staff(ctx: TenantContext = Depends(current_context)) -> TenantContext:
    """Cepoch support: ADMIN or OPERATOR of the workspace."""
    if not is_support_staff(ctx):
        raise HTTPException(status_code=403, detail="Support inbox requires ADMIN or OPERATOR")
    return ctx


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = ""
    severity: str = "NORMAL"


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class StatusBody(BaseModel):
    status: str


class EscalateBody(BaseModel):
    reason: str
    note: str = ""


@router.get("/tickets")
def list_support_tickets(
    assignee: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    needs_jeanne: bool = Query(default=False),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """Support staff see the workspace queue; everyone else sees only the tickets they raised."""
    staff = is_support_staff(ctx)
    items = support.list_tickets(
        client_id=ctx.client_id,
        assignee=assignee if staff else None,
        status=status,
        needs_jeanne=needs_jeanne if staff else False,
    )
    if not staff:
        items = [support.requester_view(t) for t in items if t.get("created_by") == ctx.user_id]
    return {
        "items": items,
        "can_manage": staff,
        "disclaimer": "Routine support stays with Cepoch. Jeanne only on escalate (FB-046).",
    }


@router.post("/tickets")
def create_support_ticket(
    body: TicketCreate,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Anyone signed in to the workspace may raise a ticket, as they can from the chat.

    It runs through the same policy check as the chat's support_create_ticket tool.
    """
    arguments = {"subject": body.subject, "body": body.body, "severity": body.severity}
    with bound(ctx):
        outcome = guarded_call(run_scope(ctx, settings), "support_create_ticket", arguments)
    if not outcome.ok:
        code = 403 if outcome.effect == PolicyEffect.DENY else 400
        raise HTTPException(status_code=code, detail=outcome.message or "The ticket was not raised.")
    ticket = support.get_ticket(outcome.data["id"])
    history = ticket.get("history") or []
    merged = bool(history) and history[-1].get("event") == "duplicate_merged"
    return {
        "ticket": ticket if is_support_staff(ctx) else support.requester_view(ticket),
        "merged": merged,
        "message": (
            "This matches an open ticket, so your details were added to it."
            if merged
            else "Ticket opened with Cepoch support."
        ),
    }


@router.get("/tickets/{ticket_id}")
def get_support_ticket(
    ticket_id: str,
    ctx: TenantContext = Depends(require_support_staff),
) -> dict[str, Any]:
    try:
        ticket = support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    if ticket["client_id"] != ctx.client_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket": ticket}


@router.post("/tickets/{ticket_id}/notes")
def add_ticket_note(
    ticket_id: str,
    body: NoteBody,
    ctx: TenantContext = Depends(require_support_staff),
) -> dict[str, Any]:
    try:
        ticket = support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    if ticket["client_id"] != ctx.client_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket": support.add_note(ticket_id=ticket_id, note=body.note, by=ctx.user_id)}


@router.post("/tickets/{ticket_id}/status")
def set_ticket_status(
    ticket_id: str,
    body: StatusBody,
    ctx: TenantContext = Depends(require_support_staff),
) -> dict[str, Any]:
    try:
        ticket = support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    if ticket["client_id"] != ctx.client_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        updated = support.set_status(ticket_id=ticket_id, status=body.status, by=ctx.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ticket": updated}


@router.post("/tickets/{ticket_id}/escalate")
def escalate_ticket(
    ticket_id: str,
    body: EscalateBody,
    ctx: TenantContext = Depends(require_support_staff),
) -> dict[str, Any]:
    try:
        ticket = support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    if ticket["client_id"] != ctx.client_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        updated = support.escalate_to_jeanne(
            ticket_id=ticket_id,
            reason=body.reason,
            by=ctx.user_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ticket": updated}


@router.post("/tickets/{ticket_id}/return-to-cepoch")
def return_ticket(
    ticket_id: str,
    body: NoteBody,
    ctx: TenantContext = Depends(require_admin),
) -> dict[str, Any]:
    try:
        ticket = support.get_ticket(ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    if ticket["client_id"] != ctx.client_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {
        "ticket": support.return_to_cepoch(
            ticket_id=ticket_id, by=ctx.user_id, note=body.note
        )
    }
