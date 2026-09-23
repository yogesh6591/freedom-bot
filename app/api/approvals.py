"""Approval queue routes (§26 /api/approvals)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import (
    bound,
    client_settings,
    current_context,
    require_approver,
    require_role,
    run_scope,
)
from bizos.actions import store as actions
from bizos.control.models import ClientSettings
from bizos.tenancy.context import TenantContext
from bizos.tools.executor import NotExecutable, can_execute, execute_approved_action
from bizos.types import ActionStatus, Role

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
        return {"items": [a.to_dict() for a in items], "count": len(items)}


@router.get("/all")
def list_all(
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    """Every action regardless of status — the "Actions" view."""
    with bound(ctx):
        items = actions.list_actions(limit=limit, offset=offset, ctx=ctx)
        return {"items": [a.to_dict() for a in items], "count": len(items)}


@router.get("/{action_id}")
def get_action(action_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """One action with its full transition history."""
    with bound(ctx):
        try:
            return actions.get_action(action_id, include_events=True, ctx=ctx).to_dict(
                include_events=True
            )
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
