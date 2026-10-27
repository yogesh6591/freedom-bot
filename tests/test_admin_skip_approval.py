"""approval_policy.admins_skip_approval: opt-in, admins only, and only for actions that would wait for approval."""

from __future__ import annotations

from bizos.control.models import ApprovalPolicy, ClientSettings
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.types import ExecutionMode, PolicyEffect


def _settings(client, *, skip: bool, mode=ExecutionMode.WAIT_FOR_APPROVAL, always=()):
    base = client.settings.to_dict()
    policy = {**(base.get("approval_policy") or {}), "admins_skip_approval": skip,
              "always_require_approval": list(always)}
    return ClientSettings.from_dict({**base, "default_execution_mode": mode.value, "approval_policy": policy})


def _decide(ctx, settings, tool="crm_create_note"):
    return evaluate(PolicyRequest(ctx=ctx, settings=settings, tool=tool, domain="operations",
                                  connected_integrations=frozenset({"crm"})))


def test_off_by_default():
    assert ApprovalPolicy().admins_skip_approval is False


def test_admin_waits_for_approval_while_the_switch_is_off(client_scratch, ctx_factory):
    decision = _decide(ctx_factory(client_scratch, "admin"), _settings(client_scratch, skip=False))
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL


def test_admin_runs_straight_away_when_the_switch_is_on(client_scratch, ctx_factory):
    decision = _decide(ctx_factory(client_scratch, "admin"), _settings(client_scratch, skip=True))
    assert decision.effect == PolicyEffect.ALLOW and decision.rule == "ADMIN_SKIPS_APPROVAL"


def test_other_roles_still_wait(client_scratch, ctx_factory):
    settings = _settings(client_scratch, skip=True)
    for role in ("drafter", "operator", "approver"):
        assert _decide(ctx_factory(client_scratch, role), settings).effect != PolicyEffect.ALLOW, role


def test_always_require_approval_still_binds_admins(client_scratch, ctx_factory):
    settings = _settings(client_scratch, skip=True, always=["crm_create_note"])
    assert _decide(ctx_factory(client_scratch, "admin"), settings).effect == PolicyEffect.REQUIRE_APPROVAL


def test_advise_mode_still_denies_admins(client_scratch, ctx_factory):
    settings = _settings(client_scratch, skip=True, mode=ExecutionMode.ADVISE)
    assert _decide(ctx_factory(client_scratch, "admin"), settings).effect == PolicyEffect.DENY
