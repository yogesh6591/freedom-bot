"""FB-039 / FB-043 / FB-044 follow-ups: one Order/DPA setting, biometrics, the
dental BAA pattern in chat, removable baselines and the automatic sale handoff."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.chat import (
    BIOMETRICS_REFUSAL,
    PHI_REFUSAL,
    _regulated_data_refusal,
)
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.onboarding import OnboardingProfile, apply_profile, get_profile
from bizos.policy.classifier import classify_text
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.tenancy.context import tenant_scope
from bizos.types import ExecutionMode, PolicyEffect


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(app_client, client, prefix: str) -> dict[str, str]:
    response = app_client.post(
        "/api/auth/login",
        json={
            "email": f"{prefix}@{client.slug}.example.com",
            "password": "bizos-dev-password",
            "client_slug": client.slug,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def restore(client_a):
    original = control.get_client(client_a.id).settings.to_dict()
    yield
    control.update_client_settings(
        client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
    )


# ------------------------------------------------------------------ FB-039


def test_order_dpa_list_is_the_single_setting():
    # Unticking the last category must not fall back to a stale legacy flag.
    cleared = ClientSettings.from_dict({"order_dpa_allows": [], "allow_phi": True})
    assert cleared.order_dpa_allows == []
    assert cleared.allow_phi is False

    # A row written before the list existed migrates from its booleans.
    legacy = ClientSettings.from_dict({"allow_phi": True, "allow_child_data": True})
    assert legacy.order_dpa_allows == ["child", "phi"]

    mixed = ClientSettings.from_dict({"order_dpa_allows": ["Biometrics", "bogus"]})
    assert mixed.order_dpa_allows == ["biometrics"]
    assert mixed.allow_biometrics is True and mixed.allow_phi is False


def test_model_vendor_comes_from_the_deployment(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "openai:gpt-4.1-mini")
    monkeypatch.delenv("LLM_VENDOR", raising=False)
    assert ClientSettings.from_dict({"llm_vendor": "hipaa-cloud"}).llm_vendor == "openai"


def test_phi_needs_a_baa_whichever_way_it_was_allowed(client_a, scope_factory):
    base = client_a.settings.to_dict()
    base.pop("order_dpa_allows")  # a row written before the Order/DPA list existed
    base["default_execution_mode"] = ExecutionMode.AUTO_WITHIN_SCOPE.value
    for allowed in ({"allow_phi": True}, {"order_dpa_allows": ["phi"]}):
        settings = ClientSettings.from_dict({**base, **allowed})
        scope = scope_factory(client_a, "operator", settings=settings)
        with tenant_scope(scope.ctx):
            decision = evaluate(
                PolicyRequest(
                    ctx=scope.ctx,
                    settings=settings,
                    tool="crm_create_note",
                    domain="sales",
                    contains_phi=True,
                    connected_integrations=scope.integrations,
                )
            )
        assert decision.effect == PolicyEffect.DENY, allowed
        assert decision.rule == "PHI_NO_BAA", allowed


def test_biometric_detection_ignores_technical_and_product_talk():
    assert classify_text("Enrol her fingerprint template for the door").contains_biometrics
    assert classify_text("Save the voiceprint for caller ID").contains_biometrics
    assert classify_text("Add Sam, phone 555-0101, biometric enrolment pending").contains_biometrics
    assert not classify_text("Check the SSH key fingerprint on the server").contains_biometrics
    assert not classify_text("Compare biometric time clock vendors").contains_biometrics
    assert not classify_text("Set up Face ID on the company phones").contains_biometrics


def test_chat_refuses_regulated_data_before_the_model():
    default = ClientSettings()
    status, reply = _regulated_data_refusal("Add patient Maria, phone 555-0101", default)
    assert (status, reply) == ("PHI_BLOCKED", PHI_REFUSAL)

    # Order/DPA allows PHI, but OpenAI has no BAA: still never sent to the model.
    dpa_phi = ClientSettings.from_dict({"order_dpa_allows": ["phi"]})
    status, reply = _regulated_data_refusal("Record the diagnosis for Maria Lopez", dpa_phi)
    assert status == "PHI_NO_BAA_BLOCKED" and "BAA" in reply

    covered = ClientSettings.from_dict({"order_dpa_allows": ["phi"], "llm_baa_covered": True})
    assert _regulated_data_refusal("Record the diagnosis for Maria Lopez", covered) is None

    assert _regulated_data_refusal("Store Jo's fingerprint scan", default) == (
        "BIOMETRICS_BLOCKED",
        BIOMETRICS_REFUSAL,
    )
    bio_ok = ClientSettings.from_dict({"order_dpa_allows": ["biometrics"]})
    assert _regulated_data_refusal("Store Jo's fingerprint scan", bio_ok) is None

    # Talking about the business is not handing over regulated data.
    assert _regulated_data_refusal("Find our patient retention rate", default) is None
    assert _regulated_data_refusal("What did the diagnosis tool vendor charge?", default) is None


def test_settings_api_edits_the_one_order_dpa_setting(app_client, client_a, restore):
    headers = _login(app_client, client_a, "admin")
    response = app_client.put("/api/policies/settings", json={"allow_phi": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["order_dpa_allows"] == ["phi"]

    response = app_client.put("/api/policies/settings", json={"order_dpa_allows": []}, headers=headers)
    body = response.json()
    assert body["order_dpa_allows"] == [] and body["allow_phi"] is False


# ------------------------------------------------------------------ FB-044


def test_removed_baselines_stay_removed(client_a, ctx_factory, restore):
    ctx = ctx_factory(client_a, "admin")
    row = {"workflow": "Invoices", "metric": "Days to close", "before_value": "12"}
    with tenant_scope(ctx):
        apply_profile(
            client_id=client_a.id,
            profile=OnboardingProfile(baselines=[row], exclusions=["No tax filing"]),
            settings=control.get_client(client_a.id).settings,
            ctx=ctx,
        )
        assert get_profile(control.get_client(client_a.id).settings).baselines
        apply_profile(
            client_id=client_a.id,
            profile=OnboardingProfile(baselines=[]),
            settings=control.get_client(client_a.id).settings,
            ctx=ctx,
        )
    settings = control.get_client(client_a.id).settings
    assert get_profile(settings).baselines == []
    assert settings.onboarding["baselines"] == []


# ------------------------------------------------------------------ FB-043

_ASSESSMENT = {"goals": ["Cut invoice cycle time"], "systems": ["HubSpot"]}


def _sale(app_client, client, *, secret=None, event="checkout", assessment=_ASSESSMENT):
    headers = {"X-FreedomBot-Sale-Secret": secret} if secret else {}
    return app_client.post(
        "/api/owner/sale-events",
        json={"event": event, "client_slug": client.slug, "assessment": assessment},
        headers=headers,
    )


def test_sale_webhook_requires_the_shared_secret(app_client, client_a, monkeypatch, restore):
    monkeypatch.setenv("SALE_WEBHOOK_SECRET", "s3cret-handoff")
    assert _sale(app_client, client_a).status_code == 401
    assert _sale(app_client, client_a, secret="wrong").status_code == 401
    monkeypatch.delenv("SALE_WEBHOOK_SECRET")
    # No configured secret: an empty header never matches.
    assert _sale(app_client, client_a, secret="").status_code == 401


def test_checkout_starts_handoff_once(app_client, client_a, monkeypatch, restore):
    monkeypatch.setenv("SALE_WEBHOOK_SECRET", "s3cret-handoff")
    first = _sale(app_client, client_a, secret="s3cret-handoff")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["auto_started"] is True and body["duplicate"] is False
    profile = get_profile(control.get_client(client_a.id).settings)
    assert profile.goals == ["Cut invoice cycle time"]
    assert control.get_client(client_a.id).settings.onboarding["handoff_source"] == "checkout"

    # The contract event for the same sale does not redo the handoff.
    again = _sale(app_client, client_a, secret="s3cret-handoff", event="contract")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert control.get_client(client_a.id).settings.onboarding["handoff_source"] == "checkout"


def test_pinned_deployment_refuses_another_clients_sale(
    app_client, client_a, client_b, monkeypatch
):
    monkeypatch.setenv("SALE_WEBHOOK_SECRET", "s3cret-handoff")
    monkeypatch.setenv("CLIENT_SLUG", client_b.slug)
    assert _sale(app_client, client_a, secret="s3cret-handoff").status_code == 404
