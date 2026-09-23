"""
Approved-Action Executor
========================

Runs an action that a human has approved (§8: "After approval, the execution
engine/operator may perform the action").

The critical property: approval unlocks **one specific queued call**, not a
general bypass. Before executing, the policy engine is consulted again with
``executing_approved_action=True``. That flag converts a REQUIRE_APPROVAL verdict
into ALLOW *only when the executing user's role carries execute-after-approval
rights*, and leaves every DENY verdict standing. So:

* a DENY that existed at proposal time still denies at execution time;
* an approval granted before a client disabled PHI does not become executable;
* a VIEWER holding an approved action's id still cannot run it.

The action must be in ``APPROVED`` status, which only
:func:`bizos.actions.store.approve` can produce, and which itself refuses
self-approval. Execution reuses the same effect function the live path uses, so
there is exactly one implementation of "what this tool does".
"""

from __future__ import annotations

import time
from typing import Optional

from bizos.actions import store as action_store
from bizos.actions.models import Action
from bizos.audit import events as audit
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.rbac.registry import get_spec
from bizos.tools.base import RunScope, ToolOutcome, _execute_effect
from bizos.types import ActionStatus, AuditEventType, PolicyEffect


class NotExecutable(PermissionError):
    """The action cannot be executed in its current state or by this caller."""


def execute_approved_action(scope: RunScope, action_id: str) -> ToolOutcome:
    """Execute one APPROVED action and record the outcome."""
    action: Action = action_store.get_action(action_id, ctx=scope.ctx)

    if action.status != ActionStatus.APPROVED:
        raise NotExecutable(
            f"Action {action_id} is {action.status}; only an APPROVED action can be executed."
        )

    spec = get_spec(action.tool)
    if spec is None:
        raise NotExecutable(f"Action {action_id} names unregistered tool {action.tool!r}")

    # Re-evaluate. Approval releases this queued call; it does not grant new
    # authority, and every hard denial still applies.
    request = PolicyRequest(
        ctx=scope.ctx,
        settings=scope.settings,
        tool=action.tool,
        arguments=action.payload,
        domain=action.domain or scope.domain,
        connected_integrations=scope.integrations,
        record_count=action.record_count,
        financial_amount=action.financial_amount,
        recipients=tuple(_recipients_of(action)),
        fields=tuple(_fields_of(action)),
        executing_approved_action=True,
        approved_action_id=action_id,
    )
    decision = evaluate(request)
    audit.log_policy_decision(decision, ctx=scope.ctx, action_id=action_id, phase="execution")

    if decision.effect != PolicyEffect.ALLOW:
        action_store.mark_executing(action_id, ctx=scope.ctx)
        action_store.mark_failed(action_id, decision.reason, ctx=scope.ctx)
        audit.log(
            AuditEventType.ACTION_FAILED,
            ctx=scope.ctx,
            action_id=action_id,
            tool=action.tool,
            decision=str(decision.effect),
            status="POLICY_BLOCKED_AT_EXECUTION",
            error=decision.reason,
        )
        return ToolOutcome(
            action.tool,
            decision.effect,
            False,
            f"Approved action {action_id} could not run: {decision.reason}",
            decision,
            action_id=action_id,
        )

    started = time.monotonic()
    action_store.mark_executing(action_id, ctx=scope.ctx)
    outcome = _execute_effect(scope, spec, action.payload, decision, started, action_id=action_id)

    if outcome.ok:
        action_store.mark_completed(action_id, {"summary": outcome.message, "data": outcome.data}, ctx=scope.ctx)
    else:
        action_store.mark_failed(action_id, outcome.message, ctx=scope.ctx)
    return outcome


def approve_and_execute(
    scope: RunScope, action_id: str, *, comment: Optional[str] = None
) -> ToolOutcome:
    """Approve then immediately execute — the "Approve" button's default path.

    Both halves keep their own checks: the approval refuses self-approval and
    non-approvers, and the execution re-evaluates policy.
    """
    action_store.approve(action_id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    return execute_approved_action(scope, action_id)


def can_execute(scope: RunScope, action: Action) -> bool:
    """Whether this caller could execute ``action`` if it were approved."""
    from bizos.rbac.permissions import resolve

    return resolve(action.tool, scope.ctx.roles, ctx=scope.ctx).can_execute_approved


def _recipients_of(action: Action) -> list[str]:
    raw = action.payload.get("recipients") or action.payload.get("attendees") or []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [str(r) for r in raw]


def _fields_of(action: Action) -> list[str]:
    field = action.payload.get("field")
    if field:
        return [str(field)]
    if action.payload.get("lifecycle") or action.payload.get("add_tags"):
        fields = ["tags"] if action.payload.get("add_tags") else []
        if action.payload.get("lifecycle"):
            fields.append("lifecycle")
        return fields
    return []
