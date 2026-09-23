"""
Action and Approval Store
=========================

The durable record of every proposed business action and its approval history
(§8).

Two invariants are enforced here rather than trusted:

1. **Only legal transitions.** Every status change is checked against
   :data:`bizos.types.ACTION_TRANSITIONS`. An illegal move raises instead of
   writing an impossible row, so "APPROVED without ever being PENDING" cannot
   exist in the table.
2. **Approval is someone else's job.** Unless the client explicitly enables
   self-approval, the approver must not be the requester. Checked in code, in the
   same transaction that records the decision.

``approval_events`` is append-only at the database level, so the transition
history cannot be edited after the fact.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import text

from bizos.actions.models import Action, ApprovalEvent, ApprovalRequest
from bizos.policy.models import PolicyDecision
from bizos.tenancy.context import TenantContext, require_context
from bizos.tenancy.registry import workspace_connection
from bizos.types import (
    ACTION_TRANSITIONS,
    ActionStatus,
    AuditEventType,
    DataClassification,
    ExecutionMode,
    RiskLevel,
    Role,
)
from bizos.util.ids import new_id
from bizos.util.timeutil import in_seconds, utcnow


class ActionNotFound(LookupError):
    pass


class IllegalTransition(RuntimeError):
    """A status change that the action lifecycle does not permit."""


class SelfApprovalRejected(PermissionError):
    """The requester tried to approve their own action."""


class NotAnApprover(PermissionError):
    """The caller lacks the role required to decide this action."""


def assert_transition(current: ActionStatus, target: ActionStatus) -> None:
    """Raise unless ``current -> target`` is a legal move."""
    if target not in ACTION_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(
            f"Cannot move an action from {current} to {target}. "
            f"Legal next states: {sorted(str(s) for s in ACTION_TRANSITIONS.get(current, ()))}"
        )


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _action_from_row(row: Any) -> Action:
    return Action(
        id=row.id,
        title=row.title,
        description=row.description,
        explanation=row.explanation,
        status=ActionStatus.parse(row.status, ActionStatus.DRAFT),  # type: ignore[arg-type]
        tool=row.tool,
        payload=row.payload or {},
        risk_level=RiskLevel.parse(row.risk_level, RiskLevel.MEDIUM),  # type: ignore[arg-type]
        classification=DataClassification.parse(row.classification, DataClassification.INTERNAL),  # type: ignore[arg-type]
        execution_mode=ExecutionMode.parse(row.execution_mode, ExecutionMode.WAIT_FOR_APPROVAL),  # type: ignore[arg-type]
        policy_reason=row.policy_reason,
        requested_by=row.requested_by,
        requested_by_role=row.requested_by_role,
        integration=row.integration,
        domain=row.domain,
        agent_id=row.agent_id,
        session_id=row.session_id,
        run_id=row.run_id,
        workflow_id=row.workflow_id,
        workflow_run_id=row.workflow_run_id,
        record_count=row.record_count,
        financial_amount=row.financial_amount,
        result=row.result,
        error=row.error,
        expires_at=row.expires_at,
        executed_at=row.executed_at,
        executed_by=row.executed_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _request_from_row(row: Any) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        action_id=row.action_id,
        status=ActionStatus.parse(row.status, ActionStatus.PENDING_APPROVAL),  # type: ignore[arg-type]
        reason=row.reason,
        required_role=row.required_role,
        requested_by=row.requested_by,
        assigned_to=row.assigned_to,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        comment=row.comment,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


def _event_from_row(row: Any) -> ApprovalEvent:
    return ApprovalEvent(
        id=row.id,
        action_id=row.action_id,
        event=row.event,
        from_status=row.from_status,
        to_status=row.to_status,
        actor_id=row.actor_id,
        actor_role=row.actor_role,
        comment=row.comment,
        payload_before=row.payload_before,
        payload_after=row.payload_after,
        created_at=row.created_at,
    )


def _record_event(
    conn: Any,
    *,
    action_id: str,
    event: str,
    actor: TenantContext,
    from_status: Optional[ActionStatus] = None,
    to_status: Optional[ActionStatus] = None,
    comment: Optional[str] = None,
    payload_before: Optional[dict] = None,
    payload_after: Optional[dict] = None,
    request_id: Optional[str] = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO approval_events (action_id, request_id, event, from_status, to_status, "
            "actor_id, actor_role, comment, payload_before, payload_after) VALUES "
            "(:a, :r, :e, :f, :t, :ai, :ar, :c, CAST(:pb AS JSONB), CAST(:pa AS JSONB))"
        ),
        {
            "a": action_id,
            "r": request_id,
            "e": event,
            "f": str(from_status) if from_status else None,
            "t": str(to_status) if to_status else None,
            "ai": actor.user_id,
            "ar": str(actor.primary_role),
            "c": comment,
            "pb": json.dumps(payload_before, default=str) if payload_before is not None else None,
            "pa": json.dumps(payload_after, default=str) if payload_after is not None else None,
        },
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_action(
    *,
    title: str,
    description: str,
    explanation: str,
    tool: str,
    payload: dict[str, Any],
    decision: PolicyDecision,
    settings: Any,
    status: ActionStatus = ActionStatus.DRAFT,
    integration: Optional[str] = None,
    domain: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    record_count: int = 1,
    financial_amount: Optional[Decimal] = None,
    ctx: Optional[TenantContext] = None,
) -> Action:
    """Record a proposed action, optionally opening its approval request.

    ``status`` is ``DRAFT`` for DRAFT_ONLY outcomes and ``PENDING_APPROVAL`` for
    REQUIRE_APPROVAL outcomes; the approval row is created only in the latter
    case, which is what keeps the queue free of things nobody is waiting on.
    """
    tenant = ctx or require_context()
    action_id = new_id("act")
    expires = (
        in_seconds(settings.approval_policy.expires_after_hours * 3600)
        if status == ActionStatus.PENDING_APPROVAL
        else None
    )

    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO actions (id, title, description, explanation, status, tool, "
                "integration, domain, payload, risk_level, classification, execution_mode, "
                "policy_reason, requested_by, requested_by_role, agent_id, session_id, run_id, "
                "workflow_id, workflow_run_id, record_count, financial_amount, expires_at) VALUES "
                "(:id, :ti, :de, :ex, :st, :to, :in, :do, CAST(:pa AS JSONB), :ri, :cl, :mo, :pr, "
                ":rb, :rr, :ag, :se, :ru, :wi, :wr, :rc, :fa, :exp)"
            ),
            {
                "id": action_id,
                "ti": title,
                "de": description,
                "ex": explanation,
                "st": str(status),
                "to": tool,
                "in": integration,
                "do": domain,
                "pa": json.dumps(payload, default=str),
                "ri": str(decision.risk),
                "cl": str(decision.classification),
                "mo": str(decision.mode),
                "pr": decision.reason,
                "rb": tenant.user_id,
                "rr": str(tenant.primary_role),
                "ag": agent_id,
                "se": session_id,
                "ru": run_id,
                "wi": workflow_id,
                "wr": workflow_run_id,
                "rc": record_count,
                "fa": financial_amount,
                "exp": expires,
            },
        )
        _record_event(
            conn,
            action_id=action_id,
            event="PROPOSED",
            actor=tenant,
            to_status=status,
            comment=decision.reason,
            payload_after=payload,
        )
        request_id = None
        if status == ActionStatus.PENDING_APPROVAL:
            request_id = new_id("apr")
            conn.execute(
                text(
                    "INSERT INTO approval_requests (id, action_id, status, reason, required_role, "
                    "requested_by, expires_at) VALUES (:i, :a, :s, :r, :rr, :rb, :e)"
                ),
                {
                    "i": request_id,
                    "a": action_id,
                    "s": str(ActionStatus.PENDING_APPROVAL),
                    "r": decision.reason,
                    "rr": settings.approval_policy.approver_role,
                    "rb": tenant.user_id,
                    "e": expires,
                },
            )
            _record_event(
                conn,
                action_id=action_id,
                event="APPROVAL_REQUESTED",
                actor=tenant,
                to_status=ActionStatus.PENDING_APPROVAL,
                comment=decision.reason,
                request_id=request_id,
            )

    _audit(
        AuditEventType.ACTION_PROPOSED if status == ActionStatus.DRAFT else AuditEventType.APPROVAL_REQUESTED,
        tenant,
        action_id=action_id,
        tool=tool,
        status=str(status),
        decision=str(decision.effect),
        risk_level=str(decision.risk),
        request={"title": title, "payload": payload},
        result={"reason": decision.reason},
    )
    return get_action(action_id, ctx=tenant)


def submit_for_approval(
    action_id: str, *, settings: Any, ctx: Optional[TenantContext] = None
) -> Action:
    """Move a DRAFT action into the approval queue."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        assert_transition(action.status, ActionStatus.PENDING_APPROVAL)
        expires = in_seconds(settings.approval_policy.expires_after_hours * 3600)
        request_id = new_id("apr")
        conn.execute(
            text(
                "INSERT INTO approval_requests (id, action_id, status, reason, required_role, "
                "requested_by, expires_at) VALUES (:i, :a, :s, :r, :rr, :rb, :e)"
            ),
            {
                "i": request_id,
                "a": action_id,
                "s": str(ActionStatus.PENDING_APPROVAL),
                "r": action.policy_reason,
                "rr": settings.approval_policy.approver_role,
                "rb": tenant.user_id,
                "e": expires,
            },
        )
        _set_status(conn, action, ActionStatus.PENDING_APPROVAL, tenant, event="SUBMITTED", request_id=request_id)
    _audit(AuditEventType.APPROVAL_REQUESTED, tenant, action_id=action_id, tool=action.tool)
    return get_action(action_id, ctx=tenant)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _check_approver(action: Action, tenant: TenantContext, settings: Any) -> None:
    """Role and self-approval checks, applied before any decision is recorded."""
    required = Role.parse(settings.approval_policy.approver_role, Role.APPROVER)
    if not (tenant.has_role(required) or tenant.is_admin):
        raise NotAnApprover(
            f"Deciding this action requires the {required} role; "
            f"{tenant.user_id} holds {sorted(str(r) for r in tenant.roles)}"
        )
    if action.requested_by == tenant.user_id and not settings.approval_policy.allow_self_approval:
        raise SelfApprovalRejected(
            "You proposed this action, so you cannot approve it. "
            "Self-approval is disabled for this workspace."
        )


def approve(
    action_id: str, *, settings: Any, comment: Optional[str] = None, ctx: Optional[TenantContext] = None
) -> Action:
    """Approve a pending action. Does **not** execute it — see the executor."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        _check_approver(action, tenant, settings)
        assert_transition(action.status, ActionStatus.APPROVED)
        _decide_request(conn, action_id, ActionStatus.APPROVED, tenant, comment)
        _set_status(conn, action, ActionStatus.APPROVED, tenant, event="APPROVED", comment=comment)
    _audit(
        AuditEventType.APPROVAL_GRANTED,
        tenant,
        action_id=action_id,
        tool=action.tool,
        approver=tenant.user_id,
        status=str(ActionStatus.APPROVED),
        result={"comment": comment},
    )
    return get_action(action_id, ctx=tenant)


def reject(
    action_id: str, *, settings: Any, comment: Optional[str] = None, ctx: Optional[TenantContext] = None
) -> Action:
    """Reject a pending action. Terminal."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        _check_approver(action, tenant, settings)
        assert_transition(action.status, ActionStatus.REJECTED)
        _decide_request(conn, action_id, ActionStatus.REJECTED, tenant, comment)
        _set_status(conn, action, ActionStatus.REJECTED, tenant, event="REJECTED", comment=comment)
    _audit(
        AuditEventType.APPROVAL_REJECTED,
        tenant,
        action_id=action_id,
        tool=action.tool,
        approver=tenant.user_id,
        status=str(ActionStatus.REJECTED),
        result={"comment": comment},
    )
    return get_action(action_id, ctx=tenant)


def request_changes(
    action_id: str, *, settings: Any, comment: str, ctx: Optional[TenantContext] = None
) -> Action:
    """Send a pending action back to DRAFT with a comment for the requester."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        _check_approver(action, tenant, settings)
        assert_transition(action.status, ActionStatus.DRAFT)
        _decide_request(conn, action_id, ActionStatus.DRAFT, tenant, comment)
        _set_status(conn, action, ActionStatus.DRAFT, tenant, event="CHANGES_REQUESTED", comment=comment)
    _audit(
        AuditEventType.APPROVAL_CHANGES_REQUESTED,
        tenant,
        action_id=action_id,
        tool=action.tool,
        approver=tenant.user_id,
        result={"comment": comment},
    )
    return get_action(action_id, ctx=tenant)


def edit_payload(
    action_id: str,
    payload: dict[str, Any],
    *,
    settings: Any,
    comment: Optional[str] = None,
    ctx: Optional[TenantContext] = None,
) -> Action:
    """Edit an action's payload before approving it (§8 "Edit Before Approval").

    The before/after payloads are both written to the transition history, so an
    edit is visible rather than silent.
    """
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        if action.status not in {ActionStatus.DRAFT, ActionStatus.PENDING_APPROVAL}:
            raise IllegalTransition(f"Cannot edit an action in status {action.status}")
        if action.status == ActionStatus.PENDING_APPROVAL:
            _check_approver(action, tenant, settings)
        conn.execute(
            text("UPDATE actions SET payload = CAST(:p AS JSONB), updated_at = NOW() WHERE id = :i"),
            {"p": json.dumps(payload, default=str), "i": action_id},
        )
        _record_event(
            conn,
            action_id=action_id,
            event="PAYLOAD_EDITED",
            actor=tenant,
            from_status=action.status,
            to_status=action.status,
            comment=comment,
            payload_before=action.payload,
            payload_after=payload,
        )
    _audit(
        AuditEventType.CONFIG_CHANGED,
        tenant,
        action_id=action_id,
        tool=action.tool,
        request={"payload_before": action.payload},
        result={"payload_after": payload},
    )
    return get_action(action_id, ctx=tenant)


def cancel(
    action_id: str, *, comment: Optional[str] = None, ctx: Optional[TenantContext] = None
) -> Action:
    """Cancel an action. The requester or an admin may do this."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        if action.requested_by != tenant.user_id and not tenant.is_admin:
            raise NotAnApprover("Only the requester or an admin may cancel this action")
        assert_transition(action.status, ActionStatus.CANCELLED)
        conn.execute(
            text(
                "UPDATE approval_requests SET status = :s, decided_by = :u, decided_at = NOW(), "
                "comment = :c, updated_at = NOW() WHERE action_id = :a AND status = :p"
            ),
            {
                "s": str(ActionStatus.CANCELLED),
                "u": tenant.user_id,
                "c": comment,
                "a": action_id,
                "p": str(ActionStatus.PENDING_APPROVAL),
            },
        )
        _set_status(conn, action, ActionStatus.CANCELLED, tenant, event="CANCELLED", comment=comment)
    return get_action(action_id, ctx=tenant)


def _decide_request(
    conn: Any, action_id: str, status: ActionStatus, tenant: TenantContext, comment: Optional[str]
) -> None:
    conn.execute(
        text(
            "UPDATE approval_requests SET status = :s, decided_by = :u, decided_at = NOW(), "
            "comment = :c, updated_at = NOW() WHERE action_id = :a AND status = :p"
        ),
        {
            "s": str(status),
            "u": tenant.user_id,
            "c": comment,
            "a": action_id,
            "p": str(ActionStatus.PENDING_APPROVAL),
        },
    )


def _set_status(
    conn: Any,
    action: Action,
    target: ActionStatus,
    tenant: TenantContext,
    *,
    event: str,
    comment: Optional[str] = None,
    request_id: Optional[str] = None,
    **columns: Any,
) -> None:
    sets = ["status = :s", "updated_at = NOW()"]
    params: dict[str, Any] = {"s": str(target), "i": action.id}
    for name, value in columns.items():
        sets.append(f"{name} = :{name}")
        params[name] = value
    conn.execute(text(f"UPDATE actions SET {', '.join(sets)} WHERE id = :i"), params)
    _record_event(
        conn,
        action_id=action.id,
        event=event,
        actor=tenant,
        from_status=action.status,
        to_status=target,
        comment=comment,
        request_id=request_id,
    )


def _locked_action(conn: Any, action_id: str) -> Action:
    """Read an action FOR UPDATE so concurrent decisions serialize."""
    row = conn.execute(
        text("SELECT * FROM actions WHERE id = :i FOR UPDATE"), {"i": action_id}
    ).first()
    if row is None:
        raise ActionNotFound(action_id)
    return _action_from_row(row)


# ---------------------------------------------------------------------------
# Execution bookkeeping (the executor drives the connector call itself)
# ---------------------------------------------------------------------------


def mark_executing(action_id: str, *, ctx: Optional[TenantContext] = None) -> Action:
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        assert_transition(action.status, ActionStatus.EXECUTING)
        _set_status(conn, action, ActionStatus.EXECUTING, tenant, event="EXECUTION_STARTED")
    return get_action(action_id, ctx=tenant)


def mark_completed(
    action_id: str, result: dict[str, Any], *, ctx: Optional[TenantContext] = None
) -> Action:
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        assert_transition(action.status, ActionStatus.COMPLETED)
        conn.execute(
            text(
                "UPDATE actions SET status = :s, result = CAST(:r AS JSONB), executed_at = NOW(), "
                "executed_by = :u, updated_at = NOW() WHERE id = :i"
            ),
            {"s": str(ActionStatus.COMPLETED), "r": json.dumps(result, default=str), "u": tenant.user_id, "i": action_id},
        )
        _record_event(
            conn,
            action_id=action_id,
            event="EXECUTION_COMPLETED",
            actor=tenant,
            from_status=action.status,
            to_status=ActionStatus.COMPLETED,
            payload_after=result,
        )
    return get_action(action_id, ctx=tenant)


def mark_failed(action_id: str, error: str, *, ctx: Optional[TenantContext] = None) -> Action:
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        action = _locked_action(conn, action_id)
        assert_transition(action.status, ActionStatus.FAILED)
        _set_status(conn, action, ActionStatus.FAILED, tenant, event="EXECUTION_FAILED", comment=error, error=error)
    return get_action(action_id, ctx=tenant)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_action(
    action_id: str, *, include_events: bool = False, ctx: Optional[TenantContext] = None
) -> Action:
    with workspace_connection(ctx, readonly=True) as conn:
        row = conn.execute(text("SELECT * FROM actions WHERE id = :i"), {"i": action_id}).first()
        if row is None:
            raise ActionNotFound(action_id)
        action = _action_from_row(row)
        request = conn.execute(
            text("SELECT * FROM approval_requests WHERE action_id = :a ORDER BY created_at DESC LIMIT 1"),
            {"a": action_id},
        ).first()
        if request is not None:
            action.approval = _request_from_row(request)
        if include_events:
            events = conn.execute(
                text("SELECT * FROM approval_events WHERE action_id = :a ORDER BY id"),
                {"a": action_id},
            ).fetchall()
            action.events = [_event_from_row(e) for e in events]
    return action


def list_actions(
    *,
    statuses: Optional[Sequence[ActionStatus]] = None,
    requested_by: Optional[str] = None,
    tool: Optional[str] = None,
    domain: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    ctx: Optional[TenantContext] = None,
) -> list[Action]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500)), "offset": max(0, offset)}
    if statuses:
        clauses.append("a.status = ANY(:statuses)")
        params["statuses"] = [str(s) for s in statuses]
    if requested_by:
        clauses.append("a.requested_by = :requested_by")
        params["requested_by"] = requested_by
    if tool:
        clauses.append("a.tool = :tool")
        params["tool"] = tool
    if domain:
        clauses.append("a.domain = :domain")
        params["domain"] = domain
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(f"SELECT a.* FROM actions a {where} ORDER BY a.created_at DESC LIMIT :limit OFFSET :offset"),
            params,
        ).fetchall()
        out: list[Action] = []
        for row in rows:
            action = _action_from_row(row)
            request = conn.execute(
                text("SELECT * FROM approval_requests WHERE action_id = :a ORDER BY created_at DESC LIMIT 1"),
                {"a": action.id},
            ).first()
            if request is not None:
                action.approval = _request_from_row(request)
            out.append(action)
    return out


def pending_approvals(*, limit: int = 100, ctx: Optional[TenantContext] = None) -> list[Action]:
    """The approval queue."""
    return list_actions(statuses=[ActionStatus.PENDING_APPROVAL], limit=limit, ctx=ctx)


def expire_stale(*, ctx: Optional[TenantContext] = None) -> int:
    """Cancel approval requests past their expiry. Returns how many."""
    tenant = ctx or require_context()
    now = utcnow()
    with workspace_connection(tenant) as conn:
        rows = conn.execute(
            text(
                "SELECT id FROM actions WHERE status = :p AND expires_at IS NOT NULL AND expires_at < :n"
            ),
            {"p": str(ActionStatus.PENDING_APPROVAL), "n": now},
        ).fetchall()
        for row in rows:
            action = _locked_action(conn, row.id)
            _decide_request(conn, row.id, ActionStatus.CANCELLED, tenant, "expired")
            _set_status(conn, action, ActionStatus.CANCELLED, tenant, event="EXPIRED", comment="approval window elapsed")
    return len(rows)


def _audit(event_type: AuditEventType, tenant: TenantContext, **fields: Any) -> None:
    from bizos.audit.events import log

    log(event_type, ctx=tenant, **fields)
