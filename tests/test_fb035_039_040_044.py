"""FB-035 / FB-039 / FB-040 / FB-044 coverage."""

from __future__ import annotations

from bizos.onboarding import OnboardingProfile, apply_profile, get_profile
from bizos.policy.classifier import classify_arguments, classify_text
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import guarded_call
from bizos.types import ExecutionMode, PolicyEffect


def test_classifier_flags_licensed_and_phi():
    legal = classify_text("Please give legal advice on this breach of contract")
    assert "legal" in legal.categories
    assert legal.requires_licensed_human

    medical = classify_text("Update the patient diagnosis in the medical record")
    assert medical.contains_phi
    assert "medical" in medical.categories

    card = classify_arguments({"body": "Store the card number and CVV for this customer"})
    assert card.contains_card_data


def test_licensed_content_forces_approval_on_writes(client_a, scope_factory):
    scope = scope_factory(client_a, "operator", domain="sales", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "crm_create_note",
            {"body": "This is legal advice: they are liable for breach of contract."},
        )
    assert outcome.effect == PolicyEffect.REQUIRE_APPROVAL
    codes = {c.code for c in outcome.decision.constraints}
    assert "LICENSED_CONTENT" in codes


def test_finance_propose_adjustment_always_needs_approval(client_a, scope_factory, settings_with):
    settings = settings_with(
        client_a.settings,
        default_execution_mode=ExecutionMode.AUTO_WITHIN_SCOPE.value,
        enabled_domains=["general", "finance"],
        risk_policy={"auto_allowed_max_risk": "HIGH"},
    )
    scope = scope_factory(client_a, "operator", domain="finance", settings=settings)
    with tenant_scope(scope.ctx):
        decision = evaluate(
            PolicyRequest(
                ctx=scope.ctx,
                settings=settings,
                tool="finance_propose_adjustment",
                domain="finance",
                connected_integrations=scope.integrations,
            )
        )
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL
    assert "LICENSED_JUDGMENT" in {c.code for c in decision.constraints}


def test_phase1_domains_are_full():
    from bizos.domains.catalog import DOMAIN_CATALOG
    from bizos.domains.packs import assert_catalog_complete, get_pack

    assert_catalog_complete()
    for entry in DOMAIN_CATALOG:
        assert entry.depth == "full", entry.name
        pack = get_pack(entry.name)
        assert pack is not None
        assert pack.tool_specs(), entry.name


def test_n8n_trigger_queues_for_approval_then_local_delivery(client_a, scope_factory, ctx_factory):
    from bizos.tools.executor import execute_approved_action
    from bizos.actions import store as actions

    scope = scope_factory(client_a, "operator", domain="operations")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "n8n_trigger_webhook",
            {"event": "lead.qualified", "payload": {"email": "a@b.com"}},
        )
        assert outcome.effect == PolicyEffect.REQUIRE_APPROVAL
        action_id = outcome.action_id
        assert action_id

    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        actions.approve(action_id, settings=client_a.settings, comment="ok", ctx=approver_ctx)

    with tenant_scope(scope.ctx):
        result = execute_approved_action(scope, action_id)
    assert result.ok
    assert result.data["status"] in {"local", "delivered"}


def test_onboarding_apply_writes_memory_and_settings(client_a, ctx_factory, settings_with):
    from bizos.control import store as control
    from bizos.control.models import ClientSettings

    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    settings = settings_with(client_a.settings)
    profile = OnboardingProfile(
        goals=["Ship Phase 1"],
        systems=["Workspace CRM", "n8n"],
        sops=[{"title": "Demo SOP", "content": "Do the demo carefully.", "key": "demo_sop"}],
        autonomy_mode=ExecutionMode.DRAFT.value,
        enabled_domains=["general", "sales", "strategy"],
        assessment={"source": "test"},
    )
    try:
        with tenant_scope(ctx):
            result = apply_profile(
                client_id=client_a.id, profile=profile, settings=settings, ctx=ctx
            )
        assert any(w["kind"] == "goal" for w in result["memory_written"])
        assert result["settings"]["default_execution_mode"] == "DRAFT"
        # Strategy is an add-on outside the base package: it waits for a change order.
        assert "sales" in result["settings"]["enabled_domains"]
        assert "strategy" not in result["settings"]["enabled_domains"]
        stored = get_profile(control.get_client(client_a.id).settings)
        assert stored.goals == ["Ship Phase 1"]
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )
