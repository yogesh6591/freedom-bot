"""Approval queue routes (§26 /api/approvals)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import (
    bound,
    client_settings,
    current_context,
    require_admin,
    require_approver,
    require_role,
    run_scope,
)
from bizos.actions import store as actions
from bizos.control.models import ClientSettings
from bizos.tenancy.context import TenantContext
from bizos.tools.executor import NotExecutable, can_execute, execute_approved_action
from bizos.types import ActionStatus, PolicyEffect, Role

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class Decision(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=2000)
    #: Approve and, if the approver also holds execute rights for this tool,
    #: run it immediately. When they do not, the action stays APPROVED for an
    #: operator to run — approving is not the same authority as executing.
    execute: bool = True


class ChangeRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


class PayloadEdit(BaseModel):
    payload: dict[str, Any]
    comment: Optional[str] = None


def _users(ctx: TenantContext) -> dict[str, Any]:
    from bizos.control import store as control

    try:
        return {u.id: u for u in control.list_users(ctx.client_id)}
    except Exception:
        return {}


def _named(items: list[Any], ctx: TenantContext) -> list[dict[str, Any]]:
    """Action dicts with requester name + readable content for the approval cards."""
    from bizos import automation
    from bizos.actions.preview import content_for, schedule_ids

    users = _users(ctx)
    try:
        schedules = automation.lookup(ctx, schedule_ids(items))
    except Exception:
        schedules = {}
    out = []
    for a in items:
        d = a.to_dict()
        user = users.get(a.requested_by)
        d["requested_by_name"] = (user.display_name or user.email) if user else None
        d["requested_by_email"] = user.email if user else None
        try:
            d["content"] = content_for(a, ctx=ctx, schedules=schedules)
        except Exception:
            d["content"] = {
                "kind": "generic",
                "headline": a.title,
                "fields": [],
                "lines": [],
                "summary": a.description or a.title,
            }
        out.append(d)
    return out


@router.get("")
def list_queue(
    status: Optional[str] = None,
    tool: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """The approval queue, or any action list filtered by status."""
    statuses = [ActionStatus.parse(status)] if status else [ActionStatus.PENDING_APPROVAL]  # type: ignore[list-item]
    with bound(ctx):
        items = actions.list_actions(
            statuses=[s for s in statuses if s], tool=tool, limit=limit, offset=offset, ctx=ctx
        )
        return {"items": _named(items, ctx), "count": len(items)}


@router.get("/all")
def list_all(
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """Every action regardless of status — the "Actions" view."""
    with bound(ctx):
        items = actions.list_actions(limit=limit, offset=offset, ctx=ctx)
        return {"items": _named(items, ctx), "count": len(items)}


#: How far back the requester's own decided requests are shown in the chat inbox.
_INBOX_RECENT = 25


@router.get("/inbox")
def inbox(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """What the chat's approval bell shows (polled every few seconds).

    ``to_decide``: requests waiting for approval that this user may decide (approvers and admins; never
    their own, which self-approval refuses). ``mine``: this user's own recent requests and their status,
    so a requester sees the moment theirs is approved or rejected. Deciding still goes through
    POST /{id}/approve and /{id}/reject with their normal checks.
    """
    can_decide = ctx.is_admin or ctx.has_role(Role.APPROVER)
    with bound(ctx):
        pending = actions.pending_approvals(limit=100, ctx=ctx) if can_decide else []
        to_decide = [a for a in pending if a.requested_by != ctx.user_id]
        mine = actions.list_actions(requested_by=ctx.user_id, limit=_INBOX_RECENT, ctx=ctx)
        return {"can_decide": can_decide, "to_decide": _named(to_decide, ctx), "mine": _named(mine, ctx)}


@router.get("/research-progress")
def research_progress(ctx: TenantContext = Depends(require_admin)) -> Optional[dict[str, Any]]:
    """How the latest prospect research is going, for the research card in the chat bell (null when none)."""
    from acquisition.db.session import SessionLocal
    from app.outreach import research_tools

    db = SessionLocal()
    try:
        return research_tools.progress(db)
    finally:
        db.close()


@router.get("/schedules")
def list_schedules(
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """The Scheduled tab on Approvals: each recurring task, how it stands, and what is waiting for it.

    Read through the same guard as the chat's schedule_list (admins see everyone's). ``waiting`` are
    requests to set up a schedule that still need approval, so they show before they exist.
    """
    from bizos.actions.preview import content_for, schedule_id
    from bizos.tools.base import guarded_call

    with bound(ctx):
        outcome = guarded_call(run_scope(ctx, settings), "schedule_list",
                               {"everyone": ctx.is_admin, "include_ended": True})
        if not outcome.ok:
            raise HTTPException(status_code=403, detail=outcome.message or "Not permitted")
        pending = actions.list_actions(statuses=[ActionStatus.PENDING_APPROVAL], limit=500, ctx=ctx)
        waiting_for: dict[str, int] = {}
        for a in pending:
            found = schedule_id(a)
            if found and a.tool != "schedule_create":
                waiting_for[found] = waiting_for.get(found, 0) + 1
        users = _users(ctx)

        def name(user_id: Optional[str]) -> Optional[str]:
            user = users.get(user_id or "")
            return (user.display_name or user.email) if user else None

        rows = []
        for row in outcome.data or []:
            rows.append({**row, "created_by_name": name(row.get("created_by")),
                         "awaiting_approval": waiting_for.get(row.get("id"), 0)})
        waiting = []
        for a in pending:
            if a.tool != "schedule_create":
                continue
            info = content_for(a, ctx=ctx, schedules={}).get("schedule") or {}
            waiting.append({**info, "action_id": a.id, "requested_at": a.to_dict().get("created_at"),
                            "created_by_name": name(a.requested_by)})
    return {"items": rows, "waiting": waiting}


class ScheduleChange(BaseModel):
    """Only the fields that change; the rest of the schedule stays as it is."""

    title: Optional[str] = Field(default=None, max_length=120)
    instruction: Optional[str] = Field(default=None, max_length=2000)
    frequency: Optional[str] = Field(default=None, max_length=20)
    time: Optional[str] = Field(default=None, max_length=20)
    weekdays: Optional[list[str]] = Field(default=None, max_length=7)
    every_minutes: Optional[int] = None
    timezone: Optional[str] = Field(default=None, max_length=64)


def _schedule_outcome(ctx: TenantContext, settings: ClientSettings, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Change or stop a schedule through the same guard and policy check as the chat's schedule tools."""
    from bizos.tools.base import guarded_call

    with bound(ctx):
        outcome = guarded_call(run_scope(ctx, settings), tool, arguments)
    if outcome.effect == PolicyEffect.REQUIRE_APPROVAL and outcome.action_id:
        return {"pending_approval": True, "action_id": outcome.action_id,
                "message": "Sent for approval. It changes once an approver accepts it."}
    if not outcome.ok:
        code = 403 if outcome.effect == PolicyEffect.DENY else 400
        raise HTTPException(status_code=code, detail=outcome.message or "That did not work.")
    return {**(outcome.data or {}), "message": outcome.message}


@router.put("/schedules/{schedule_id}")
def change_schedule(
    schedule_id: str,
    body: ScheduleChange,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Edit one schedule (the chat's schedule_update tool)."""
    return _schedule_outcome(ctx, settings, "schedule_update",
                             {"schedule_id": schedule_id, **body.model_dump(exclude_none=True)})


@router.delete("/schedules/{schedule_id}")
def stop_schedule(
    schedule_id: str,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Stop one schedule so it never runs again (the chat's schedule_cancel tool)."""
    return _schedule_outcome(ctx, settings, "schedule_cancel", {"schedule_id": schedule_id})


@router.get("/{action_id}")
def get_action(action_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """One action with its full transition history."""
    with bound(ctx):
        try:
            action = actions.get_action(action_id, include_events=True, ctx=ctx)
            enriched = _named([action], ctx)[0]
            enriched["events"] = [
                e.to_dict() if hasattr(e, "to_dict") else e for e in (action.events or [])
            ]
            return enriched
        except actions.ActionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, actions.ActionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (actions.SelfApprovalRejected, actions.NotAnApprover, NotExecutable)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, actions.IllegalTransition):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/{action_id}/approve")
def approve(
    action_id: str,
    body: Decision,
    ctx: TenantContext = Depends(require_approver),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Approve, and execute if this approver may also execute.

    §4 is explicit that "approval does not automatically mean this person must
    personally execute the action". An APPROVER typically holds APPROVE_ONLY on
    protected tools, so attempting execution as them would be refused by the
    policy engine — correctly, but it would also mark a perfectly good action
    FAILED. So execution is attempted only when the approver actually holds
    execute rights for that tool; otherwise the action stays APPROVED and waits
    for an operator to call /execute.
    """
    with bound(ctx):
        try:
            action = actions.approve(action_id, settings=settings, comment=body.comment, ctx=ctx)
            scope = run_scope(ctx, settings, domain=action.domain or "general")
            if not body.execute:
                return {
                    "action": action.to_dict(),
                    "executed": False,
                    "pending_execution": True,
                    "message": "Approved. An operator can now run it.",
                }
            if not can_execute(scope, action):
                return {
                    "action": action.to_dict(),
                    "executed": False,
                    "pending_execution": True,
                    "message": (
                        f"Approved. Your role may approve {action.tool} but not execute it, "
                        "so it is waiting for an operator to run."
                    ),
                }
            outcome = execute_approved_action(scope, action_id)
            return {
                "action": actions.get_action(action_id, include_events=True, ctx=ctx).to_dict(
                    include_events=True
                ),
                "executed": True,
                "result": outcome.to_dict(),
            }
        except Exception as exc:
            raise _handle(exc) from exc


@router.post("/{action_id}/execute")
def execute(
    action_id: str,
    ctx: TenantContext = Depends(require_role(Role.APPROVER, Role.OPERATOR)),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Run an already-APPROVED action.

    Approvers and operators may execute once the tool's effective permission
    grants ``EXECUTE_AFTER_APPROVAL`` or ``ALLOWED``. The policy engine runs
    again here, so an approval granted before the client tightened a rule does
    not become a way past the new rule.
    """
    with bound(ctx):
        try:
            action = actions.get_action(action_id, ctx=ctx)
            scope = run_scope(ctx, settings, domain=action.domain or "general")
            if not can_execute(scope, action):
                raise NotExecutable(
                    f"Your role may not execute {action.tool}. Ask an operator, "
                    "or grant EXECUTE_AFTER_APPROVAL for this tool."
                )
            outcome = execute_approved_action(scope, action_id)
            return {
                "action": actions.get_action(action_id, include_events=True, ctx=ctx).to_dict(
                    include_events=True
                ),
                "executed": outcome.ok,
                "result": outcome.to_dict(),
            }
        except Exception as exc:
            raise _handle(exc) from exc


@router.post("/{action_id}/reject")
def reject(
    action_id: str,
    body: Decision,
    ctx: TenantContext = Depends(require_approver),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    with bound(ctx):
        try:
            return actions.reject(action_id, settings=settings, comment=body.comment, ctx=ctx).to_dict()
        except Exception as exc:
            raise _handle(exc) from exc


@router.post("/{action_id}/request-changes")
def request_changes(
    action_id: str,
    body: ChangeRequest,
    ctx: TenantContext = Depends(require_approver),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    with bound(ctx):
        try:
            return actions.request_changes(
                action_id, settings=settings, comment=body.comment, ctx=ctx
            ).to_dict()
        except Exception as exc:
            raise _handle(exc) from exc


@router.post("/{action_id}/edit")
def edit(
    action_id: str,
    body: PayloadEdit,
    ctx: TenantContext = Depends(require_approver),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Edit before approval. Both payloads are recorded in the history."""
    with bound(ctx):
        try:
            return actions.edit_payload(
                action_id, body.payload, settings=settings, comment=body.comment, ctx=ctx
            ).to_dict()
        except Exception as exc:
            raise _handle(exc) from exc


@router.post("/{action_id}/submit")
def submit(
    action_id: str,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Move a DRAFT action into the approval queue."""
    with bound(ctx):
        try:
            return actions.submit_for_approval(action_id, settings=settings, ctx=ctx).to_dict()
        except Exception as exc:
            raise _handle(exc) from exc


@router.post("/{action_id}/cancel")
def cancel(
    action_id: str, ctx: TenantContext = Depends(current_context)
) -> dict[str, Any]:
    with bound(ctx):
        try:
            return actions.cancel(action_id, ctx=ctx).to_dict()
        except Exception as exc:
            raise _handle(exc) from exc
