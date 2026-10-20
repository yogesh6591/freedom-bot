"""
Approval Tool Effects
=====================

Work the approval queue from chat: list actions in any status, read one with
its history, and approve, reject, send back, submit or cancel — exactly what the
Approvals page can do, under the same rules.

Authority is never decided here. Every decision goes through
:mod:`bizos.actions.store`, which checks the configured approver role,
self-approval and data-area visibility, and an approved action is executed by
:mod:`bizos.tools.executor`, which re-runs the policy engine for the queued tool.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

from bizos.actions import store as actions
from bizos.actions.models import Action
from bizos.connectors.base import ConnectorResult
from bizos.rbac.registry import get_spec
from bizos.tools.base import RunScope, effect
from bizos.types import ActionStatus, Role

_STATUS_WORDS: dict[str, tuple[ActionStatus, ...]] = {
    "pending": (ActionStatus.PENDING_APPROVAL,),
    "pending_approval": (ActionStatus.PENDING_APPROVAL,),
    "waiting": (ActionStatus.PENDING_APPROVAL,),
    "queue": (ActionStatus.PENDING_APPROVAL,),
    "draft": (ActionStatus.DRAFT,),
    "drafts": (ActionStatus.DRAFT,),
    "changes_requested": (ActionStatus.DRAFT,),
    "approved": (ActionStatus.APPROVED,),
    "executing": (ActionStatus.EXECUTING,),
    "completed": (ActionStatus.COMPLETED,),
    "complete": (ActionStatus.COMPLETED,),
    "done": (ActionStatus.COMPLETED,),
    "executed": (ActionStatus.COMPLETED,),
    "failed": (ActionStatus.FAILED,),
    "rejected": (ActionStatus.REJECTED,),
    "declined": (ActionStatus.REJECTED,),
    "cancelled": (ActionStatus.CANCELLED,),
    "canceled": (ActionStatus.CANCELLED,),
}

_ALL_WORDS = {"all", "any", "everything", "history"}


def _statuses(raw: Any) -> Optional[list[ActionStatus]]:
    """Status words from the model -> statuses. ``None`` means every status."""
    words = raw if isinstance(raw, list) else str(raw or "pending").replace(",", " ").split()
    picked: list[ActionStatus] = []
    for word in words:
        key = str(word).strip().casefold().replace(" ", "_").replace("-", "_")
        if key in _ALL_WORDS:
            return None
        for status in _STATUS_WORDS.get(key, (ActionStatus.parse(key),) if ActionStatus.parse(key) else ()):
            if status not in picked:
                picked.append(status)
    return picked or [ActionStatus.PENDING_APPROVAL]


def _names(scope: RunScope) -> dict[str, str]:
    from bizos.control import store as control

    try:
        return {u.id: u.display_name or u.email for u in control.list_users(scope.ctx.client_id)}
    except Exception:
        return {}


def _approver_role(scope: RunScope) -> Role:
    return Role.parse(scope.settings.approval_policy.approver_role, Role.APPROVER)


def _can_decide(scope: RunScope) -> bool:
    return scope.ctx.is_admin or scope.ctx.has_role(_approver_role(scope))


def _summary_of(action: Action, scope: RunScope) -> str:
    from bizos.actions.preview import content_for

    try:
        content = content_for(action, ctx=scope.ctx)
        return str(content.get("summary") or content.get("headline") or action.title)
    except Exception:
        return action.description or action.title


def _render(action: Action, scope: RunScope, names: dict[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "action_id": action.id,
        "title": action.title,
        "status": str(action.status),
        "tool": action.tool,
        "what": _summary_of(action, scope),
        "requested_by": names.get(action.requested_by, action.requested_by),
        "requested_by_role": action.requested_by_role,
        "requested_by_you": action.requested_by == scope.ctx.user_id,
        "why_it_needs_review": action.policy_reason,
        "created_at": action.created_at.isoformat() if action.created_at else None,
        "updated_at": action.updated_at.isoformat() if action.updated_at else None,
    }
    if action.approval and action.approval.decided_by:
        entry["decided_by"] = names.get(action.approval.decided_by, action.approval.decided_by)
        entry["decided_at"] = action.approval.decided_at.isoformat() if action.approval.decided_at else None
        entry["decision_comment"] = action.approval.comment
    if action.executed_by:
        entry["executed_by"] = names.get(action.executed_by, action.executed_by)
        entry["executed_at"] = action.executed_at.isoformat() if action.executed_at else None
    if action.status == ActionStatus.FAILED and action.error:
        entry["error"] = action.error
    if action.status == ActionStatus.COMPLETED and action.result:
        entry["result"] = action.result.get("summary") if isinstance(action.result, dict) else action.result
    customer = (action.payload or {}).get("customer")
    if action.tool == "finance_draft_invoice" and customer:
        from bizos.tools.domains import finance_list_pending_billables

        entry["customer"] = customer
        if action.status == ActionStatus.PENDING_APPROVAL:
            listed = finance_list_pending_billables(scope, {"customer": customer})
            entry["amount"] = (listed.data or {}).get("total")
            entry["line_items"] = (listed.data or {}).get("items")
    return entry


def _visible(action: Action, scope: RunScope) -> bool:
    area = getattr(get_spec(action.tool), "data_area", None)
    return not area or scope.ctx.can_see_area(area)


def _resolve(
    scope: RunScope, args: dict[str, Any], statuses: Optional[list[ActionStatus]] = None
) -> tuple[Optional[Action], Optional[str]]:
    """Find the one action the user means, by id or by a phrase from its title."""
    ref = str(args.get("action_id") or args.get("id") or "").strip()
    if ref:
        try:
            action = actions.get_action(ref, include_events=True, ctx=scope.ctx)
            if _visible(action, scope):
                return action, None
        except actions.ActionNotFound:
            pass
    phrase = str(args.get("match") or ("" if ref.startswith("act") else ref)).strip().casefold()
    if not phrase:
        return None, (
            f"No action {ref!r} that you can see." if ref
            else "Say which action — give its id, or call approvals_list to find it."
        )
    candidates = [
        a for a in actions.list_actions(statuses=statuses, limit=200, ctx=scope.ctx)
        if _visible(a, scope)
        and (phrase in a.title.casefold() or phrase in (a.description or "").casefold())
    ]
    if len(candidates) == 1:
        return actions.get_action(candidates[0].id, include_events=True, ctx=scope.ctx), None
    if not candidates:
        return None, f"No action matching {phrase!r} that you can see."
    listed = "; ".join(f"{a.id} — {a.title} ({a.status})" for a in candidates[:8])
    return None, f"More than one action matches {phrase!r}; ask the user which one: {listed}"


def _decision_error(exc: Exception, action: Action, scope: RunScope, verb: str) -> ConnectorResult:
    if isinstance(exc, actions.NotAnApprover):
        roles = ", ".join(sorted(str(r) for r in scope.ctx.roles)) or "no role"
        return ConnectorResult.failure(
            f"Not permitted: you cannot {verb} {action.id}. Deciding approvals needs the "
            f"{_approver_role(scope)} role and you hold {roles}. Nothing was changed."
        )
    if isinstance(exc, actions.SelfApprovalRejected):
        return ConnectorResult.failure(
            f"Not permitted: you requested {action.id} yourself, and this workspace does not "
            "allow self-approval. Another approver must decide it. Nothing was changed."
        )
    if isinstance(exc, actions.IllegalTransition):
        return ConnectorResult.failure(
            f"{action.id} is {action.status}, so it cannot be {verb}d now. "
            "Only an action that is PENDING_APPROVAL can be decided."
        )
    if isinstance(exc, actions.ActionNotFound):
        return ConnectorResult.failure(f"No action {action.id} that you can see.")
    raise exc


@effect("approvals_list")
def approvals_list(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Actions in the requested statuses, as the caller may see them."""
    statuses = _statuses(args.get("status"))
    mine = bool(args.get("mine"))
    try:
        limit = max(1, min(int(args.get("limit") or 25), 100))
    except (TypeError, ValueError):
        limit = 25
    names = _names(scope)
    found = [
        a for a in actions.list_actions(
            statuses=statuses,
            requested_by=scope.ctx.user_id if mine else None,
            limit=limit,
            ctx=scope.ctx,
        )
        if _visible(a, scope)
    ]
    label = "all statuses" if statuses is None else ", ".join(str(s) for s in statuses)
    can_decide = _can_decide(scope)
    return ConnectorResult(
        ok=True,
        data={
            "statuses": label,
            "only_mine": mine,
            "actions": [_render(a, scope, names) for a in found],
            "you_can_approve": can_decide,
            "approver_role": str(_approver_role(scope)),
            "self_approval_allowed": bool(scope.settings.approval_policy.allow_self_approval),
        },
        summary=(
            f"{len(found)} action(s) in {label}"
            + ("; this user can approve or reject pending ones" if can_decide
               else f"; this user cannot approve (needs {_approver_role(scope)})")
        ),
    )


@effect("approvals_get")
def approvals_get(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """One action with its full history of who did what."""
    action, problem = _resolve(scope, args)
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    names = _names(scope)
    data = _render(action, scope, names)
    data["payload"] = action.payload
    data["history"] = [
        {
            "event": e.event,
            "from": e.from_status,
            "to": e.to_status,
            "by": names.get(e.actor_id, e.actor_id),
            "role": e.actor_role,
            "comment": e.comment,
            "at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in action.events
    ]
    return ConnectorResult(ok=True, data=data, summary=f"{action.id} ({action.status}): {action.title}")


@effect("approvals_approve")
def approvals_approve(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Approve a pending action, then run it when this user may execute it."""
    from bizos.tools.executor import can_execute, execute_approved_action

    action, problem = _resolve(scope, args, [ActionStatus.PENDING_APPROVAL])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    comment = str(args.get("comment") or "").strip() or None
    try:
        approved = actions.approve(action.id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    except Exception as exc:
        return _decision_error(exc, action, scope, "approve")

    run = dataclasses.replace(scope, domain=approved.domain or scope.domain, counters={})
    if not can_execute(run, approved):
        return ConnectorResult(
            ok=True,
            data={"action_id": approved.id, "status": str(approved.status), "executed": False},
            summary=(
                f"approved {approved.id} ({approved.title}). This role may approve "
                f"{approved.tool} but not carry it out, so it is waiting for an operator to run it."
            ),
        )
    outcome = execute_approved_action(run, approved.id)
    final = actions.get_action(approved.id, ctx=scope.ctx)
    return ConnectorResult(
        ok=True,
        data={
            "action_id": final.id,
            "status": str(final.status),
            "executed": outcome.ok,
            "result": outcome.message,
        },
        summary=(
            f"approved and carried out {final.id} ({final.title}): {outcome.message}"
            if outcome.ok
            else f"approved {final.id} ({final.title}), but running it failed: {outcome.message}"
        ),
    )


@effect("approvals_reject")
def approvals_reject(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    action, problem = _resolve(scope, args, [ActionStatus.PENDING_APPROVAL])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    comment = str(args.get("comment") or args.get("reason") or "").strip() or None
    try:
        rejected = actions.reject(action.id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    except Exception as exc:
        return _decision_error(exc, action, scope, "reject")
    return ConnectorResult(
        ok=True,
        data={"action_id": rejected.id, "status": str(rejected.status)},
        summary=f"rejected {rejected.id} ({rejected.title}); it will not run",
    )


@effect("approvals_request_changes")
def approvals_request_changes(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    comment = str(args.get("comment") or args.get("reason") or "").strip()
    if not comment:
        return ConnectorResult.failure("Say what needs to change — the requester sees this comment.")
    action, problem = _resolve(scope, args, [ActionStatus.PENDING_APPROVAL])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    try:
        returned = actions.request_changes(action.id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    except Exception as exc:
        return _decision_error(exc, action, scope, "send back")
    return ConnectorResult(
        ok=True,
        data={"action_id": returned.id, "status": str(returned.status)},
        summary=f"sent {returned.id} ({returned.title}) back to the requester as a draft with the requested changes",
    )


@effect("approvals_submit")
def approvals_submit(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Put a saved draft into the approval queue."""
    action, problem = _resolve(scope, args, [ActionStatus.DRAFT])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    if action.requested_by != scope.ctx.user_id and not scope.ctx.is_admin:
        return ConnectorResult.failure(
            f"Not permitted: {action.id} is someone else's draft. Only its requester or an admin can submit it."
        )
    if action.status != ActionStatus.DRAFT:
        return ConnectorResult.failure(f"{action.id} is {action.status}; only a DRAFT can be submitted for approval.")
    try:
        submitted = actions.submit_for_approval(action.id, settings=scope.settings, ctx=scope.ctx)
    except actions.IllegalTransition as exc:
        return ConnectorResult.failure(str(exc))
    return ConnectorResult(
        ok=True,
        data={"action_id": submitted.id, "status": str(submitted.status)},
        summary=(
            f"submitted {submitted.id} ({submitted.title}) for approval; it is now pending approval "
            f"by a {_approver_role(scope)}"
        ),
    )


@effect("approvals_cancel")
def approvals_cancel(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    action, problem = _resolve(
        scope, args, [ActionStatus.DRAFT, ActionStatus.PENDING_APPROVAL, ActionStatus.APPROVED, ActionStatus.FAILED]
    )
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    comment = str(args.get("comment") or args.get("reason") or "").strip() or None
    try:
        cancelled = actions.cancel(action.id, comment=comment, ctx=scope.ctx)
    except actions.NotAnApprover:
        return ConnectorResult.failure(
            f"Not permitted: only the requester of {action.id} or an admin can cancel it. Nothing was changed."
        )
    except actions.IllegalTransition:
        return ConnectorResult.failure(f"{action.id} is {action.status} and can no longer be cancelled.")
    return ConnectorResult(
        ok=True,
        data={"action_id": cancelled.id, "status": str(cancelled.status)},
        summary=f"cancelled {cancelled.id} ({cancelled.title})",
    )
