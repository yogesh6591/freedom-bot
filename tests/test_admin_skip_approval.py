"""Admins always skip approval (no workspace setting), but only for actions that would just wait for it."""

from __future__ import annotations

from freedombot.control.models import ApprovalPolicy, ClientSettings
from freedombot.policy.engine import evaluate
from freedombot.policy.models import PolicyRequest
from freedombot.types import ExecutionMode, PolicyEffect


def _settings(client, *, mode=ExecutionMode.WAIT_FOR_APPROVAL, always=()):
    base = client.settings.to_dict()
    policy = {**(base.get("approval_policy") or {}), "always_require_approval": list(always)}
    return ClientSettings.from_dict({**base, "default_execution_mode": mode.value, "approval_policy": policy})


def _decide(ctx, settings, tool="crm_create_note"):
    return evaluate(PolicyRequest(ctx=ctx, settings=settings, tool=tool, domain="operations",
                                  connected_integrations=frozenset({"crm"})))


def test_there_is_no_setting_for_it():
    assert not hasattr(ApprovalPolicy(), "admins_skip_approval")


def test_an_old_stored_switch_is_ignored(client_scratch, ctx_factory):
    base = client_scratch.settings.to_dict()
    old_off = ClientSettings.from_dict(
        {**base, "approval_policy": {**(base.get("approval_policy") or {}), "admins_skip_approval": False}})
    decision = _decide(ctx_factory(client_scratch, "admin"), old_off)
    assert decision.effect == PolicyEffect.ALLOW and decision.rule == "ADMIN_SKIPS_APPROVAL"


def test_admin_runs_straight_away_by_default(client_scratch, ctx_factory):
    decision = _decide(ctx_factory(client_scratch, "admin"), _settings(client_scratch))
    assert decision.effect == PolicyEffect.ALLOW and decision.rule == "ADMIN_SKIPS_APPROVAL"


def test_other_roles_still_wait(client_scratch, ctx_factory):
    settings = _settings(client_scratch)
    for role in ("drafter", "operator", "approver"):
        assert _decide(ctx_factory(client_scratch, role), settings).effect != PolicyEffect.ALLOW, role


def test_always_require_approval_still_binds_admins(client_scratch, ctx_factory):
    settings = _settings(client_scratch, always=["crm_create_note"])
    assert _decide(ctx_factory(client_scratch, "admin"), settings).effect == PolicyEffect.REQUIRE_APPROVAL


def test_a_licensed_decision_still_needs_approval_for_admins(client_scratch, ctx_factory):
    base = _settings(client_scratch).to_dict()
    settings = ClientSettings.from_dict(
        {**base, "enabled_domains": [*base["enabled_domains"], "finance"],
         "purchased_domains": [*base["purchased_domains"], "finance"]})
    decision = evaluate(PolicyRequest(ctx=ctx_factory(client_scratch, "admin"), settings=settings,
                                      tool="finance_propose_adjustment", domain="finance"))
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL
    assert decision.rule != "ADMIN_SKIPS_APPROVAL"


def test_advise_mode_still_denies_admins(client_scratch, ctx_factory):
    settings = _settings(client_scratch, mode=ExecutionMode.ADVISE)
    assert _decide(ctx_factory(client_scratch, "admin"), settings).effect == PolicyEffect.DENY
