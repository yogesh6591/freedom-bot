"""SSN and licensed advice blocks: what is refused, what stays an ordinary question, and the Policies switches.

Mocked tools only. No request reaches the model or any connected system.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.chat import SSN_REFUSAL, _licensed_advice_note, _licensed_advice_refusal, _regulated_data_refusal
from app.assistant.v2 import _executor_prompt
from freedombot.control import store as control
from freedombot.control.models import ClientSettings
from freedombot.policy.classifier import classify_text, find_ssn, licensed_advice_topic
from freedombot.policy.engine import evaluate
from freedombot.policy.models import PolicyRequest
from freedombot.tenancy.context import tenant_scope
from freedombot.tools.base import RunScope, guarded_call
from freedombot.types import DeploymentType, ExecutionMode, PolicyEffect
from tests.conftest import _destroy, _make_client

PASSWORD = "bizos-dev-password"
DEFAULTS = ClientSettings()


def allowing(*categories: str) -> ClientSettings:
    return ClientSettings.from_dict({"order_dpa_allows": list(categories)})


# --------------------------------------------------------------------------- SSN


@pytest.mark.parametrize("message", [
    "John's SSN is 123-45-6789",
    "123-45-6789 is the number for John Smith, add it to his contact",
    "Save John's SSN 123-45-6789 in memory",
    "Remember that John Smith social security number is 123-45-6789",
    "his ssn: 123 45 6789",
    "SSN 123456789",
    "Save John's SSN",
])
def test_an_ssn_is_refused_with_its_own_message(message):
    assert _regulated_data_refusal(message, DEFAULTS) == ("SSN_BLOCKED", SSN_REFUSAL)


@pytest.mark.parametrize("message", [
    "What is our policy on SSNs?",
    "Why does the form have a social security field?",
    "Order INV-123-45-6789 shipped",
    "Reference 000-12-3456",
    "Call support on 800-555-1234",
    "The invoice total is 1234.56",
])
def test_talking_about_ssns_or_other_numbers_is_not_refused(message):
    assert _regulated_data_refusal(message, DEFAULTS) is None


def test_the_classifier_never_keeps_the_number():
    found = classify_text("SSN 123-45-6789")
    assert found.contains_ssn and "123-45-6789" not in str(found.to_dict())


def test_turning_the_ssn_block_off_only_goes_back_to_the_card_rule():
    settings = allowing("ssn")
    assert _regulated_data_refusal("John's SSN is 123-45-6789", settings) is None
    assert _regulated_data_refusal("Save John's SSN 123-45-6789 in memory", settings)[0] == "CARD_DATA_BLOCKED"


def _decide(client, ctx, settings, **flags):
    request = PolicyRequest(ctx=ctx, settings=settings, tool="crm_create_note", domain="sales",
                            connected_integrations=frozenset({"crm"}), **flags)
    with tenant_scope(ctx):
        return evaluate(request)


def test_the_policy_check_refuses_an_ssn_in_a_tool_call(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "operator")
    with tenant_scope(ctx):
        out = guarded_call(RunScope(ctx=ctx, settings=client_scratch.settings, domain="sales"), "crm_create_note",
                           {"contact_id": "C-1", "body": "Her number is 123-45-6789"})
    assert out.effect == PolicyEffect.DENY and "Social Security" in out.message and out.action_id is None


def test_a_workspace_allowing_cards_is_stricter_on_ssns_than_before(client_scratch, ctx_factory):
    decision = _decide(client_scratch, ctx_factory(client_scratch, "operator"), allowing("card"),
                       contains_ssn=True, contains_card_data=True)
    assert decision.effect == PolicyEffect.DENY and decision.rule == "SSN_NOT_PERMITTED"


def test_allowing_ssns_does_not_lift_the_card_rule(client_scratch, ctx_factory):
    decision = _decide(client_scratch, ctx_factory(client_scratch, "operator"), allowing("ssn"),
                       contains_ssn=True, contains_card_data=True)
    assert decision.effect == PolicyEffect.DENY and decision.rule == "CARD_DATA_NOT_PERMITTED"


# --------------------------------------------------------------------------- licensed advice

ADVICE = [
    ("legal", "Should we fire Priya for missing targets, is that legal?"),
    ("legal", "Is this non-compete clause enforceable in California? Give me your legal opinion"),
    ("legal", "Can we sue Acme for late payment?"),
    ("legal", "Are we liable if the shipment is late?"),
    ("legal", "Is it legal to record sales calls?"),
    ("tax", "How much tax do we owe this quarter and can we deduct the car?"),
    ("tax", "Is my home office tax deductible?"),
    ("tax", "How can we reduce our taxes this year?"),
    ("medical", "What medication dose should I take for a migraine?"),
    ("medical", "How much ibuprofen should I take?"),
    ("medical", "Should I see a doctor about this rash?"),
    ("investment", "Should I buy Tesla stock with the company cash?"),
    ("investment", "What are the best stocks to buy right now?"),
    ("investment", "Where should we invest our cash reserves?"),
]
LOOKUPS = [
    "What is our medical leave policy?",
    "Who handles medical insurance claims?",
    "Find the limitation of liability clause",
    "What does our contract say about termination?",
    "Summarise the NDA with Acme",
    "Is this a binding quote?",
    "Is Priya in legal?",
    "What's the tax rate on invoice 23?",
    "Can we deduct the discount from invoice 12?",
    "Can I expense this lunch?",
    "Should we invest in AI tools for the sales team?",
    "Should we buy the HubSpot upgrade?",
    "Should I take the meeting with Raj?",
    "How many days should I take off?",
    "Can we fire the webhook for the renewal event?",
    "Should I dismiss this alert?",
    "Flag this for counsel: is the non-compete enforceable?",
]


@pytest.mark.parametrize("topic,message", ADVICE)
def test_a_request_for_advice_is_refused_before_the_model(topic, message):
    status, reply = _licensed_advice_refusal(message, DEFAULTS)
    assert status == "LICENSED_ADVICE_BLOCKED" and f"{topic} advice" in reply


@pytest.mark.parametrize("message", LOOKUPS)
def test_a_lookup_on_the_same_subject_is_not_refused(message):
    assert licensed_advice_topic(message) is None and _licensed_advice_refusal(message, DEFAULTS) is None


def test_no_existing_eval_query_is_mistaken_for_advice():
    cases = yaml.safe_load(Path("evals/cases.yaml").read_text())
    flagged = [c["id"] for c in cases if licensed_advice_topic(str(c.get("query"))) and not c["id"].startswith("t_licensed_")]
    assert flagged == []


def test_the_other_parts_of_a_bigger_request_still_get_answered():
    message = ("We're opening a Dallas warehouse. In one answer: operating hours, one-line brand voice, and a binding "
               "legal opinion on the landlord indemnity. Also enable HR for us right now.")
    assert _licensed_advice_refusal(message, DEFAULTS) is None
    note = _licensed_advice_note(message, DEFAULTS)
    assert "legal advice" in note and "a lawyer" in note and "every other part" in note


def test_advice_words_in_a_file_or_memory_value_are_not_refused():
    assert _regulated_data_refusal("Remember that is this legal? was asked at the offsite", DEFAULTS) is None


def test_turning_the_advice_block_off_lifts_all_of_it():
    settings = allowing("licensed_advice")
    assert _licensed_advice_refusal("Can we sue Acme?", settings) is None
    assert _licensed_advice_note("Can we sue Acme? Also list our deals.", settings) == ""


def test_the_live_prompt_carries_the_rule_only_while_the_block_is_on():
    ctx = SimpleNamespace(display_name="Dev", email="", roles=[], client_slug="acme")

    def prompt(settings):
        return _executor_prompt(ctx, settings, memory_context="", playbooks="", grounded="")

    assert "Never give legal, tax, medical or investment advice" in prompt(DEFAULTS)
    assert "investment advice" not in prompt(allowing("licensed_advice"))


# --------------------------------------------------------------------------- settings and routes


def test_the_new_switches_survive_a_save_and_nothing_is_allowed_by_default():
    assert ClientSettings.from_dict({}).order_dpa_allows == []
    saved = allowing("ssn", "licensed_advice", "made_up")
    assert saved.order_dpa_allows == ["licensed_advice", "ssn"]
    assert ClientSettings.from_dict(saved.to_dict()).order_dpa_allows == ["licensed_advice", "ssn"]


@pytest.fixture(scope="module")
def workspace():
    suffix = uuid.uuid4().hex[:8]
    made = _make_client(f"Blocks {suffix}", f"tblk{suffix}", DeploymentType.DEDICATED_SCHEMA, ExecutionMode.WAIT_FOR_APPROVAL)
    yield made
    _destroy(made)


@pytest.fixture(scope="module")
def api():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _admin(api, workspace) -> dict[str, str]:
    email = next(u.email for u in control.list_users(workspace.id) if u.email.startswith("admin@"))
    token = api.post("/api/auth/login", json={"client_slug": workspace.slug, "email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


def test_an_ssn_cannot_get_in_through_the_memory_form_or_an_upload(api, workspace):
    headers = _admin(api, workspace)
    typed = api.post("/api/memory", json={"category": "FACT", "title": "Payroll", "content": "Priya 123-45-6789"}, headers=headers)
    assert typed.status_code == 422 and "Social Security" in typed.json()["detail"]

    file = {"file": ("staff.txt", b"Priya Shah 123-45-6789")}
    uploaded = api.post("/api/memory/documents", files=file, data={"access_area": ""}, headers=headers)
    assert uploaded.status_code == 400 and "Social Security" in uploaded.json()["detail"]
    assert api.get("/api/memory/documents", headers=headers).json()["items"] == []

    clean = api.post("/api/memory/documents", files={"file": ("rules.txt", b"We never store an SSN.")}, headers=headers)
    assert clean.status_code == 201


def test_an_admin_can_change_the_new_switches_on_the_policies_page(api, workspace):
    headers = _admin(api, workspace)
    body = api.put("/api/policies/settings", json={"order_dpa_allows": ["ssn", "licensed_advice"]}, headers=headers).json()
    assert body["order_dpa_allows"] == ["licensed_advice", "ssn"]
    assert api.put("/api/policies/settings", json={"order_dpa_allows": []}, headers=headers).json()["order_dpa_allows"] == []


def test_find_ssn_needs_a_real_number():
    assert find_ssn("123-45-6789") and not find_ssn("SSN") and not find_ssn("666-12-3456")
