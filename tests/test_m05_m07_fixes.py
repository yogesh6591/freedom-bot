"""
QA run 2026-09-30 fixes:

* M05-08 — actions in a restricted data area are hidden from people without it.
* M06-05 — email requests route to the pack that can send, and a reply may not
  claim a submission that never happened.
* M07-13 — children's personal data is refused unless explicitly enabled.
* M07-20 — SSNs, medical record numbers and PHI never reach the audit log or
  chat history verbatim.
"""

from __future__ import annotations

import json

import pytest

from bizos import chat_history
from bizos.actions import store as actions
from bizos.agents.claims import CORRECTION, DRAFT_ONLY_CORRECTION, enforce_claims
from bizos.agents.persona import route
from bizos.audit.events import search as audit_search
from bizos.domains.catalog import DOMAIN_NAMES
from bizos.memory import store as memory
from bizos.policy.classifier import classify_arguments, classify_text
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import guarded_call
from bizos.types import ActionStatus
from bizos.util.redaction import redact, scrub_identifiers


# ---------------------------------------------------------------- M05-08


def _finance_action(client_a, scope_factory, settings_with):
    settings = settings_with(
        client_a.settings,
        enabled_domains=["general", "finance"],
        purchased_domains=list(DOMAIN_NAMES),
    )
    scope = scope_factory(client_a, "operator", domain="finance", settings=settings)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "finance_propose_adjustment",
            {"customer": "Acme Logistics", "amount": 321, "reason": "QA M05-08", "kind": "credit"},
        )
    assert outcome.action_id, outcome.message
    return outcome.action_id


def test_finance_action_hidden_from_users_without_finance(
    client_a, scope_factory, settings_with, ctx_factory
):
    action_id = _finance_action(client_a, scope_factory, settings_with)

    for prefix in ("viewer", "employee", "ops"):
        ctx = ctx_factory(client_a, prefix)
        with tenant_scope(ctx):
            listed = {a.id for a in actions.list_actions(limit=500, ctx=ctx)}
            assert action_id not in listed, prefix
            pending = {a.id for a in actions.pending_approvals(ctx=ctx)}
            assert action_id not in pending, prefix
            with pytest.raises(actions.ActionNotFound):
                actions.get_action(action_id, ctx=ctx)

    for prefix in ("finance", "approver", "admin"):
        ctx = ctx_factory(client_a, prefix)
        with tenant_scope(ctx):
            assert action_id in {a.id for a in actions.list_actions(limit=500, ctx=ctx)}, prefix
            assert actions.get_action(action_id, ctx=ctx).tool == "finance_propose_adjustment"


def test_approver_without_area_cannot_decide(client_a, scope_factory, settings_with, ctx_factory):
    from bizos.control import store as control

    action_id = _finance_action(client_a, scope_factory, settings_with)
    approver = next(
        u for u in control.list_users(client_a.id) if u.email.startswith("approver@")
    )
    original = sorted(approver.data_scopes)
    try:
        control.set_user_scopes(approver.id, ["legal"])
        ctx = ctx_factory(client_a, "approver")
        with tenant_scope(ctx):
            with pytest.raises(actions.ActionNotFound):
                actions.approve(action_id, settings=client_a.settings, ctx=ctx)
            assert actions.get_action(action_id, ctx=ctx_factory(client_a, "admin")).status == (
                ActionStatus.PENDING_APPROVAL
            )
    finally:
        control.set_user_scopes(approver.id, original)


# ---------------------------------------------------------------- M06-05


@pytest.mark.parametrize(
    "message",
    [
        "Send an email to john@northwind.com saying hello.",
        "Email Maria a thank-you note for the webinar.",
        "Reply to the Contoso thread and confirm Thursday.",
    ],
)
def test_email_requests_route_to_sales(message):
    enabled = ["general", "operations", "sales", "intake", "planning"]
    assert route(message, enabled) == "sales"


def test_false_submission_claim_is_corrected():
    reply = (
        "Sending an email requires approval. Your request has been submitted for approval. "
        "It will be sent once approved."
    )
    fixed, changed = enforce_claims(
        reply, message="Send an email to john@northwind.com", activity=[], new_actions=[]
    )
    assert changed
    assert "has been submitted" not in fixed
    assert CORRECTION in fixed
    assert fixed.startswith("Sending an email requires approval.")


def test_false_decision_record_claim_is_corrected():
    reply = "Decision recorded: On 2026-09-15 we switched carrier to FastFreight."
    fixed, changed = enforce_claims(
        reply, message="Record this decision: switch carrier", activity=[], new_actions=[]
    )
    assert changed and fixed == CORRECTION


def test_real_submission_is_left_alone():
    reply = "Your email is now awaiting approval."
    fixed, changed = enforce_claims(
        reply,
        message="Send an email to John",
        activity=[{"tool": "email_send_message", "ok": True}],
        new_actions=[{"id": "act_1", "status": "PENDING_APPROVAL"}],
    )
    assert not changed and fixed == reply


def test_questions_about_the_queue_are_left_alone():
    reply = "Two actions have been submitted and are waiting: an invoice and an email."
    fixed, changed = enforce_claims(
        reply, message="What is waiting for approval?", activity=[], new_actions=[]
    )
    assert not changed


# ---------------------------------------------------------------- M07-13


def test_child_data_is_detected():
    assert classify_text(
        "Add this child to our CRM: Timmy Test, age 9, school Lincoln Elementary, parent phone 555-0100."
    ).contains_child_data
    assert classify_text("New contact: Sam, a 12-year-old student").contains_child_data
    # Business uses of the same words are not flagged.
    assert not classify_text("What is our childcare benefit policy?").contains_child_data
    assert not classify_text("Add Maria Silva, VP Sales, maria@contoso.com").contains_child_data


def test_policy_denies_writes_with_child_data(client_a, scope_factory, settings_with):
    settings = settings_with(client_a.settings, default_execution_mode="AUTO_WITHIN_SCOPE")
    scope = scope_factory(client_a, "operator", domain="sales", settings=settings)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "crm_create_contact",
            {"first_name": "Timmy", "last_name": "Test", "title": "age 9, Lincoln Elementary"},
        )
    assert not outcome.ok
    assert outcome.action_id is None
    assert "children" in (outcome.message or "").lower() or "child" in json.dumps(
        getattr(outcome, "policy", None) or outcome.message
    ).lower()


def test_child_data_allowed_only_when_enabled(client_a, scope_factory, settings_with):
    from bizos.policy.engine import evaluate
    from bizos.policy.models import PolicyRequest

    base = scope_factory(client_a, "operator", domain="sales")
    for allowed in (False, True):
        settings = settings_with(client_a.settings, order_dpa_allows=["child"] if allowed else [])
        request = PolicyRequest(
            ctx=base.ctx,
            settings=settings,
            tool="crm_create_contact",
            domain="sales",
            connected_integrations=base.integrations,
            contains_child_data=True,
        )
        codes = {c.code for c in evaluate(request).constraints}
        assert ("CHILD_DATA_NOT_PERMITTED" in codes) is (not allowed)


def test_chat_refuses_child_data_before_the_model():
    from app.api.chat import CHILD_DATA_REFUSAL, _child_data_refusal
    from bizos.control.models import ClientSettings

    message = "Add this child to our CRM: Timmy Test, age 9, school Lincoln Elementary, parent phone 555-0100."
    assert _child_data_refusal(message, ClientSettings()) == CHILD_DATA_REFUSAL
    assert _child_data_refusal(message, ClientSettings(allow_child_data=True)) is None
    assert _child_data_refusal("What is our childcare benefit policy?", ClientSettings()) is None


# ---------------------------------------------------------------- M07-20


def test_identifiers_are_redacted():
    text = (
        "Store this: Jane Test, SSN 123-45-6789. Patient John Test, DOB 01/02/1980, "
        "MRN 55512345. Remember my SSN 987654320."
    )
    scrubbed = scrub_identifiers(text)
    for raw in ("123-45-6789", "01/02/1980", "55512345", "987654320"):
        assert raw not in scrubbed
    assert "Jane Test" in scrubbed  # the rest of the message stays readable
    assert "123-45-6789" not in json.dumps(redact({"request": {"query": text}}))


def test_memory_search_audit_keeps_no_regulated_text(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "operator")
    queries = [
        "Store this for the new hire: Jane Test, SSN 123-45-6789, start date Oct 1.",
        "Record this patient note: John Test, DOB 01/02/1980, diagnosis Type 2 diabetes, MRN 55512345.",
    ]
    with tenant_scope(ctx):
        for q in queries:
            memory.search(q, ctx=ctx)
        rows = audit_search(ctx=ctx_factory(client_a, "admin"), event_types=["MEMORY_READ"], limit=50)
    blob = json.dumps(rows, default=str)
    for raw in ("123-45-6789", "55512345", "01/02/1980", "Type 2 diabetes"):
        assert raw not in blob


def test_chat_history_keeps_no_identifiers(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "operator")
    chat_history.record_turn(
        ctx,
        session_id="ses_qa_ssn",
        user_message="Remember my SSN for later: 987-65-4320.",
        reply="I cannot store sensitive information such as Social Security Numbers.",
        domain="general",
        meta={},
    )
    stored = json.dumps(chat_history.get_session(ctx, "ses_qa_ssn"))
    assert "987-65-4320" not in stored
    assert "Remember my SSN for later" in stored


def test_draft_described_as_awaiting_approval_is_corrected():
    reply = (
        "An email draft to john@northwind.com has been prepared. It is not sent yet and is "
        "awaiting approval before sending."
    )
    fixed, changed = enforce_claims(
        reply,
        message="Send an email to john@northwind.com",
        activity=[{"tool": "email_create_draft", "ok": True}],
        new_actions=[],
    )
    assert changed
    assert "awaiting approval" not in fixed
    assert DRAFT_ONLY_CORRECTION in fixed
