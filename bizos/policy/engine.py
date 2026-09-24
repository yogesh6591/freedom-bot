"""
Policy Engine
=============

The single chokepoint every proposed write passes through (§6).

    Agent proposes action → evaluate() → ALLOW | DENY | DRAFT_ONLY | REQUIRE_APPROVAL

Two properties matter more than anything else here:

**The model is not in the loop.** ``evaluate`` is a pure function over the request,
the client's configuration and the tool registry. There is no prompt, no model
call and no text the model authored that can change the outcome — the model's
only contribution is ``arguments``, which is data the rules inspect, never
instructions they obey.

**The result is the most restrictive applicable constraint.** Every rule runs, and
the minimum effect wins. A new rule can therefore only tighten the system;
there is no ordering mistake that lets something through.
"""

from __future__ import annotations

from typing import Optional

from bizos.policy.models import (
    EFFECT_ORDER,
    Constraint,
    EvaluationInputs,
    PolicyDecision,
    PolicyRequest,
)
from bizos.policy.modes import narrowest, resolve_mode
from bizos.policy.rules import RULES
from bizos.rbac.permissions import EffectivePermission, resolve as resolve_permission
from bizos.rbac.registry import ToolSpec, get_spec
from bizos.types import DataClassification, ExecutionMode, PolicyEffect, RiskLevel


class PolicyViolation(Exception):
    """Raised when code attempts an action the engine denied.

    Carries the decision so the caller can render the reason and record it.
    """

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


def evaluate(req: PolicyRequest, *, domain_mode: Optional[ExecutionMode] = None) -> PolicyDecision:
    """Decide what may happen for one proposed tool call."""
    spec = get_spec(req.tool)
    if spec is None:
        # No metadata means no basis for a judgment, so the answer is no.
        return PolicyDecision(
            effect=PolicyEffect.DENY,
            tool=req.tool,
            reason=f"{req.tool} is not a registered tool",
            rule="TOOL_UNREGISTERED",
            mode=req.settings.mode,
            risk=RiskLevel.CRITICAL,
            classification=DataClassification.RESTRICTED,
            constraints=[
                Constraint("TOOL_UNREGISTERED", PolicyEffect.DENY, "unregistered tool")
            ],
        )

    permission: EffectivePermission = resolve_permission(req.tool, req.ctx.roles, ctx=req.ctx)
    mode = resolve_mode(
        req.settings.mode, domain_override=domain_mode, requested=req.requested_mode
    )
    # FB-038: a per-tool mode assignment may narrow further, never widen.
    tool_mode = req.settings.tool_mode(req.tool)
    if tool_mode is not None:
        mode = narrowest(mode, tool_mode)
    risk = req.risk_override or spec.risk

    inputs = EvaluationInputs(spec=spec, permission=permission, mode=mode, risk=risk)

    constraints: list[Constraint] = []
    for rule in RULES:
        constraint = rule(req, inputs)
        if constraint is not None:
            constraints.append(constraint)

    # Most restrictive wins. Ties keep the earlier (more fundamental) rule.
    constraints.sort(key=lambda c: c.rank)
    if constraints and constraints[0].rank < EFFECT_ORDER[PolicyEffect.ALLOW]:
        binding = constraints[0]
        effect, reason, rule_code = binding.effect, binding.reason, binding.code
    else:
        effect = PolicyEffect.ALLOW
        reason = f"{spec.title} is permitted for the {req.ctx.primary_role} role in {mode} mode"
        rule_code = "DEFAULT_ALLOW"

    # An action already blessed by an approver is executed, not re-queued —
    # otherwise approval could never complete. Denials still stand: approval
    # does not unlock something the role or the guardrails forbid outright.
    if (
        req.executing_approved_action
        and effect == PolicyEffect.REQUIRE_APPROVAL
        and permission.can_execute_approved
    ):
        effect = PolicyEffect.ALLOW
        reason = f"executing approved action {req.approved_action_id or ''}".strip()
        rule_code = "APPROVED_ACTION_EXECUTION"

    return PolicyDecision(
        effect=effect,
        tool=req.tool,
        reason=reason,
        rule=rule_code,
        mode=mode,
        risk=risk,
        classification=spec.classification,
        constraints=constraints,
        approver_role=(
            req.settings.approval_policy.approver_role
            if effect == PolicyEffect.REQUIRE_APPROVAL
            else None
        ),
        draft_counterpart=(
            spec.draft_counterpart if effect == PolicyEffect.DRAFT_ONLY else None
        ),
    )


def require_allowed(req: PolicyRequest) -> PolicyDecision:
    """Evaluate and raise :class:`PolicyViolation` unless the effect is ALLOW.

    For code paths that have no meaningful non-ALLOW branch (the executor
    running an approved action, for instance).
    """
    decision = evaluate(req)
    if not decision.allowed:
        raise PolicyViolation(decision)
    return decision


def readable_tools(req_template: PolicyRequest) -> list[ToolSpec]:
    """Tool specs whose *read* use the caller may make in the current context.

    Used by the surface builder to decide what the model is even shown.
    """
    from bizos.rbac.registry import TOOL_SPECS

    out: list[ToolSpec] = []
    for spec in TOOL_SPECS:
        probe = PolicyRequest(
            ctx=req_template.ctx,
            settings=req_template.settings,
            tool=spec.name,
            domain=req_template.domain,
            connected_integrations=req_template.connected_integrations,
        )
        decision = evaluate(probe)
        if decision.effect != PolicyEffect.DENY:
            out.append(spec)
    return out
