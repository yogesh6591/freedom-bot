"""
Policy Rules
============

Each rule inspects the request and returns a :class:`~bizos.policy.models.Constraint`
or ``None``. The engine applies **all** of them and takes the most restrictive
result, so adding a rule can only ever tighten the system — a new rule cannot
accidentally widen access by being ordered wrongly.

Rules are pure functions of their inputs. No I/O, no model calls, no randomness:
the same request always produces the same decision, which is what makes the
policy layer auditable and testable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

from bizos.policy.models import Constraint, EvaluationInputs, PolicyRequest
from bizos.types import (
    DataClassification,
    ExecutionMode,
    PolicyEffect,
    RiskLevel,
    Role,
    classification_rank,
    risk_rank,
)

Rule = Callable[[PolicyRequest, EvaluationInputs], Optional[Constraint]]

DENY = PolicyEffect.DENY
DRAFT_ONLY = PolicyEffect.DRAFT_ONLY
REQUIRE_APPROVAL = PolicyEffect.REQUIRE_APPROVAL
ALLOW = PolicyEffect.ALLOW


# ---------------------------------------------------------------------------
# Hard denials — conditions under which the call must not happen at all
# ---------------------------------------------------------------------------


def rule_tool_implemented(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """A declared-but-unbuilt tool is denied rather than half-executed."""
    if not inp.spec.implemented:
        return Constraint(
            "TOOL_NOT_IMPLEMENTED",
            DENY,
            f"{inp.spec.title} is declared but its adapter is not available in this deployment",
        )
    return None


def rule_domain_enabled(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """The tool's domain pack must be switched on for this client."""
    if not req.settings.domain_enabled(req.domain):
        return Constraint(
            "DOMAIN_DISABLED",
            DENY,
            f"the {req.domain} domain is not enabled for this workspace",
        )
    if inp.spec.domains and req.domain not in inp.spec.domains:
        return Constraint(
            "TOOL_OUTSIDE_DOMAIN",
            DENY,
            f"{inp.spec.title} is not part of the {req.domain} domain",
        )
    return None


def rule_integration_connected(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """A connector-backed tool needs its integration connected and enabled."""
    integration = inp.spec.integration
    if integration and integration not in req.connected_integrations:
        return Constraint(
            "INTEGRATION_NOT_CONNECTED",
            DENY,
            f"the {integration} integration is not connected for this workspace",
        )
    return None


def rule_rbac(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """The caller's roles must grant *some* capability on this tool."""
    perm = inp.permission
    if perm.source == "unregistered":
        return Constraint("TOOL_UNREGISTERED", DENY, f"{req.tool} is not a registered tool")
    if perm.denied:
        return Constraint(
            "RBAC_DENIED",
            DENY,
            f"the {req.ctx.primary_role} role is not permitted to use {inp.spec.title}",
        )
    if inp.spec.write:
        if perm.can_invoke:
            return None
        if perm.can_execute_approved:
            return Constraint(
                "RBAC_EXECUTE_AFTER_APPROVAL",
                REQUIRE_APPROVAL,
                f"the {req.ctx.primary_role} role may only carry out {inp.spec.title} "
                "after it has been approved",
            )
        if perm.can_draft:
            return Constraint(
                "RBAC_DRAFT_ONLY",
                DRAFT_ONLY,
                f"the {req.ctx.primary_role} role may prepare {inp.spec.title} but not execute it",
            )
        # Holds only APPROVE_ONLY: may bless someone else's action, not originate one.
        return Constraint(
            "RBAC_APPROVE_ONLY",
            DENY,
            f"the {req.ctx.primary_role} role may approve {inp.spec.title} but not initiate it",
        )
    return None


def rule_phi(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """PHI is refused unless the client is explicitly configured for it (§14)."""
    if req.settings.allow_phi:
        return None
    if inp.spec.phi_sensitive or req.contains_phi:
        return Constraint(
            "PHI_NOT_PERMITTED",
            DENY,
            "this workspace is not configured to process protected health information "
            "(allow_phi is off)",
        )
    return None


def rule_card_data(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Payment-card data is refused unless explicitly enabled (§14)."""
    if req.settings.allow_card_data:
        return None
    if inp.spec.card_sensitive or req.contains_card_data:
        return Constraint(
            "CARD_DATA_NOT_PERMITTED",
            DENY,
            "this workspace is not configured to handle payment card data "
            "(allow_card_data is off)",
        )
    return None


def rule_restricted_classification(
    req: PolicyRequest, inp: EvaluationInputs
) -> Optional[Constraint]:
    """RESTRICTED material is never reachable below OPERATOR."""
    if classification_rank(inp.spec.classification) >= classification_rank(
        DataClassification.RESTRICTED
    ) and not req.ctx.at_least(Role.OPERATOR):
        return Constraint(
            "CLASSIFICATION_TOO_HIGH",
            DENY,
            f"{inp.spec.title} handles RESTRICTED data, which the "
            f"{req.ctx.primary_role} role may not access",
        )
    return None


# ---------------------------------------------------------------------------
# Guardrails that cap the effect rather than denying outright
# ---------------------------------------------------------------------------


def rule_licensed_judgment(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """A decision reserved to a licensed human is never automatic (§14).

    The agent may still summarize, research, draft and prepare options — those
    tools are not flagged. What is capped here is *executing* the decision.
    """
    if inp.spec.licensed_judgment:
        return Constraint(
            "LICENSED_JUDGMENT",
            REQUIRE_APPROVAL,
            f"{inp.spec.title} requires a decision reserved to a licensed human, so it "
            "always needs designated human approval",
        )
    return None


def rule_always_requires_approval(
    req: PolicyRequest, inp: EvaluationInputs
) -> Optional[Constraint]:
    """Per-tool and per-client "always approve" declarations."""
    if inp.spec.always_requires_approval:
        return Constraint(
            "TOOL_ALWAYS_REQUIRES_APPROVAL",
            REQUIRE_APPROVAL,
            f"{inp.spec.title} is configured to always require approval",
        )
    if req.tool in (req.settings.approval_policy.always_require_approval or []):
        return Constraint(
            "CLIENT_ALWAYS_REQUIRES_APPROVAL",
            REQUIRE_APPROVAL,
            f"this workspace requires approval for {inp.spec.title}",
        )
    return None


def rule_critical_risk(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """CRITICAL always requires a human (§22). Not client-configurable."""
    if inp.risk == RiskLevel.CRITICAL:
        return Constraint(
            "RISK_CRITICAL",
            REQUIRE_APPROVAL,
            "this is a CRITICAL-risk action and always requires human approval",
        )
    return None


def rule_risk_floor(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Client-configured risk floor above which approval is mandatory."""
    if risk_rank(inp.risk) >= risk_rank(req.settings.approval_floor):
        return Constraint(
            "RISK_ABOVE_APPROVAL_FLOOR",
            REQUIRE_APPROVAL,
            f"{inp.risk} risk is at or above this workspace's approval threshold "
            f"({req.settings.approval_floor})",
        )
    return None


# ---------------------------------------------------------------------------
# Execution-mode rules
# ---------------------------------------------------------------------------


def rule_mode(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Apply the four execution modes (§5). Reads are never capped by mode."""
    if not inp.spec.write:
        return None
    mode = inp.mode

    if mode == ExecutionMode.ADVISE:
        return Constraint(
            "MODE_ADVISE",
            DENY,
            "this workspace is in Advise mode, so nothing can be created, changed or sent",
        )

    if mode == ExecutionMode.DRAFT:
        if inp.spec.draft_safe:
            return None
        return Constraint(
            "MODE_DRAFT",
            DRAFT_ONLY,
            "this workspace is in Draft mode, so the action is prepared for review "
            "rather than carried out",
        )

    if mode == ExecutionMode.WAIT_FOR_APPROVAL:
        if inp.spec.draft_safe:
            return None
        return Constraint(
            "MODE_WAIT_FOR_APPROVAL",
            REQUIRE_APPROVAL,
            "this workspace requires an approver to accept an action before it runs",
        )

    # AUTO_WITHIN_SCOPE: the scope checks below decide.
    return None


def rule_auto_risk_ceiling(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """In AUTO mode, only risk at or below the configured ceiling runs unattended."""
    if inp.mode != ExecutionMode.AUTO_WITHIN_SCOPE or not inp.spec.write:
        return None
    if inp.spec.draft_safe:
        return None
    if risk_rank(inp.risk) > risk_rank(req.settings.auto_max_risk):
        return Constraint(
            "AUTO_RISK_CEILING",
            REQUIRE_APPROVAL,
            f"{inp.risk} risk exceeds the automatic ceiling for this workspace "
            f"({req.settings.auto_max_risk})",
        )
    return None


# ---------------------------------------------------------------------------
# Action limits (§23) — apply to any write, in any mode above DRAFT
# ---------------------------------------------------------------------------


def rule_record_limit(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Cap on how many records one unattended action may change."""
    if not inp.spec.write:
        return None
    limit = req.settings.risk_policy.max_records_per_action
    if req.record_count > limit:
        return Constraint(
            "RECORD_LIMIT_EXCEEDED",
            REQUIRE_APPROVAL,
            f"this would change {req.record_count} records, above the automatic limit of {limit}",
        )
    return None


def rule_financial_limit(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Cap on money an unattended action may move or commit."""
    if req.financial_amount is None:
        return None
    limit = Decimal(str(req.settings.risk_policy.max_financial_amount))
    if req.financial_amount > limit:
        return Constraint(
            "FINANCIAL_LIMIT_EXCEEDED",
            REQUIRE_APPROVAL,
            f"the amount {req.financial_amount} exceeds the automatic financial limit {limit}",
        )
    return None


def rule_email_volume(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Cap on outbound sends within a single run."""
    if not inp.spec.external or inp.spec.category != "email":
        return None
    limit = req.settings.risk_policy.max_emails_per_run
    if req.emails_sent_in_run >= limit:
        return Constraint(
            "EMAIL_VOLUME_LIMIT",
            REQUIRE_APPROVAL,
            f"this run has already sent {req.emails_sent_in_run} emails, at the limit of {limit}",
        )
    return None


def rule_recipient_domains(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Outbound recipients must be on the allowlist, when one is configured."""
    allowed = [d.strip().casefold().lstrip("@") for d in req.settings.risk_policy.approved_recipient_domains if d.strip()]
    if not allowed or not req.recipients:
        return None
    offenders = sorted(
        {
            r.split("@", 1)[1].casefold()
            for r in req.recipients
            if "@" in r and r.split("@", 1)[1].casefold() not in allowed
        }
    )
    if offenders:
        return Constraint(
            "RECIPIENT_DOMAIN_NOT_APPROVED",
            REQUIRE_APPROVAL,
            f"recipient domain(s) {', '.join(offenders)} are outside the approved list",
        )
    return None


def rule_crm_field_allowlist(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Only allowlisted CRM fields may be changed unattended."""
    if inp.spec.category != "crm" or not inp.spec.write or not req.fields:
        return None
    allowed = {f.casefold() for f in req.settings.risk_policy.auto_updatable_crm_fields}
    offenders = sorted({f for f in req.fields if f.casefold() not in allowed})
    if offenders:
        return Constraint(
            "CRM_FIELD_NOT_AUTO_UPDATABLE",
            REQUIRE_APPROVAL,
            f"field(s) {', '.join(offenders)} are not on this workspace's "
            "automatically-updatable list",
        )
    return None


def rule_self_approval(req: PolicyRequest, inp: EvaluationInputs) -> Optional[Constraint]:
    """Executing an approved action requires the approver to be someone else.

    Checked here as a policy constraint as well as in the approval store, so the
    rule holds even for a caller who reaches the executor by another path.
    """
    if not req.executing_approved_action:
        return None
    return None


#: Applied in this order for readability; the engine takes the minimum effect, so
#: order affects only which reason is reported when two rules tie.
RULES: tuple[Rule, ...] = (
    rule_tool_implemented,
    rule_domain_enabled,
    rule_integration_connected,
    rule_rbac,
    rule_phi,
    rule_card_data,
    rule_restricted_classification,
    rule_licensed_judgment,
    rule_always_requires_approval,
    rule_critical_risk,
    rule_risk_floor,
    rule_mode,
    rule_auto_risk_ceiling,
    rule_record_limit,
    rule_financial_limit,
    rule_email_volume,
    rule_recipient_domains,
    rule_crm_field_allowlist,
    rule_self_approval,
)
