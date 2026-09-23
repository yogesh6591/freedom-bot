"""
Policy Request and Decision
===========================

The typed input and output of :func:`bizos.policy.engine.evaluate`.

A :class:`PolicyDecision` is a complete, self-describing verdict: the effect, the
rule that produced it, a human-readable reason, and every constraint that was
evaluated. That completeness is what lets the same object serve the tool layer,
the approval record, the audit log and the message shown to the user, instead of
each re-deriving the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from bizos.control.models import ClientSettings
from bizos.rbac.permissions import EffectivePermission
from bizos.rbac.registry import ToolSpec
from bizos.tenancy.context import TenantContext
from bizos.types import DataClassification, ExecutionMode, PolicyEffect, RiskLevel

#: Restrictiveness ordering. The engine takes the MINIMUM across all applicable
#: constraints, so the most restrictive rule always wins and rule order cannot
#: accidentally let something through.
EFFECT_ORDER: dict[PolicyEffect, int] = {
    PolicyEffect.DENY: 0,
    PolicyEffect.DRAFT_ONLY: 1,
    PolicyEffect.REQUIRE_APPROVAL: 2,
    PolicyEffect.ALLOW: 3,
}


@dataclass(frozen=True)
class Constraint:
    """One rule's verdict, with the reason it reached it."""

    code: str
    effect: PolicyEffect
    reason: str

    @property
    def rank(self) -> int:
        return EFFECT_ORDER[self.effect]


@dataclass
class PolicyRequest:
    """Everything the engine needs to decide about one proposed tool call.

    Built by the tool layer, never by the model: an LLM supplies ``arguments``,
    but ``ctx``, ``roles`` and ``settings`` come from verified credentials and the
    client record. That separation is what stops a prompt from voting on its own
    permissions.
    """

    ctx: TenantContext
    settings: ClientSettings
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    #: Requested mode for this run. May only narrow the workspace default.
    requested_mode: Optional[ExecutionMode] = None
    #: The domain pack this call is being made under.
    domain: str = "general"
    #: How many records the call would change. Drives the bulk limit (§23).
    record_count: int = 1
    #: Money the call would move or commit.
    financial_amount: Optional[Decimal] = None
    #: External recipients (email addresses, calendar invitees).
    recipients: tuple[str, ...] = ()
    #: Named fields the call would modify (CRM field allowlist).
    fields: tuple[str, ...] = ()
    #: Emails already sent in this run, for the per-run send cap.
    emails_sent_in_run: int = 0
    #: Set by the content classifier when the payload looks like PHI/card data.
    contains_phi: bool = False
    contains_card_data: bool = False
    #: Connected integration ids in this workspace.
    connected_integrations: frozenset[str] = frozenset()
    #: Overrides the tool's declared risk when the caller knows better
    #: (e.g. a bulk operation escalating from MEDIUM to HIGH).
    risk_override: Optional[RiskLevel] = None
    #: Whether this call is executing an already-APPROVED action.
    executing_approved_action: bool = False
    approved_action_id: Optional[str] = None


@dataclass
class PolicyDecision:
    """The verdict, with full provenance."""

    effect: PolicyEffect
    tool: str
    reason: str
    #: The rule code that set the final (most restrictive) effect.
    rule: str
    mode: ExecutionMode
    risk: RiskLevel
    classification: DataClassification
    #: Every constraint the engine evaluated, most restrictive first.
    constraints: list[Constraint] = field(default_factory=list)
    #: The role that must sign off, when the effect is REQUIRE_APPROVAL.
    approver_role: Optional[str] = None
    #: The tool to call instead, when the effect is DRAFT_ONLY.
    draft_counterpart: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.effect == PolicyEffect.ALLOW

    @property
    def denied(self) -> bool:
        return self.effect == PolicyEffect.DENY

    @property
    def needs_approval(self) -> bool:
        return self.effect == PolicyEffect.REQUIRE_APPROVAL

    @property
    def draft_only(self) -> bool:
        return self.effect == PolicyEffect.DRAFT_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": str(self.effect),
            "tool": self.tool,
            "reason": self.reason,
            "rule": self.rule,
            "mode": str(self.mode),
            "risk": str(self.risk),
            "classification": str(self.classification),
            "approver_role": self.approver_role,
            "draft_counterpart": self.draft_counterpart,
            "constraints": [
                {"code": c.code, "effect": str(c.effect), "reason": c.reason}
                for c in self.constraints
            ],
        }

    def user_message(self) -> str:
        """A short explanation suitable for showing to the end user in chat."""
        if self.effect == PolicyEffect.ALLOW:
            return f"Allowed: {self.reason}"
        if self.effect == PolicyEffect.DENY:
            return f"Not permitted: {self.reason}"
        if self.effect == PolicyEffect.DRAFT_ONLY:
            extra = f" You can review the draft and send it yourself." if self.draft_counterpart else ""
            return f"Prepared as a draft instead of executing: {self.reason}.{extra}"
        return f"This needs approval before it can run: {self.reason}"


@dataclass
class EvaluationInputs:
    """Resolved inputs the engine derived, exposed for testing and for audit."""

    spec: ToolSpec
    permission: EffectivePermission
    mode: ExecutionMode
    risk: RiskLevel
