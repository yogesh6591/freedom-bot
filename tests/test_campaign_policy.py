"""The six campaign tools go through the policy check, and a send needs the user's own approval.

- Every campaign call runs through guarded_call (ToolSpecs in bizos/rbac/registry.py), admin-only.
- Admins' workflow is unchanged: the tools are offered as before and a send runs directly in
  WAIT_FOR_APPROVAL mode (nothing is queued).
- send_approved needs approval_evidence that matches the user's latest message; the evidence is
  logged with every send attempt.

Uses the scripted model from test_unified_assistant.py; the Instantly send is stubbed.
"""

from __future__ import annotations

import pytest

from app.assistant import assistant as agent
from bizos.actions import store as actions
from bizos.audit import events as audit
from bizos.domains.packs import PACKS
from bizos.rbac.registry import get_spec
from bizos.tenancy.context import tenant_scope
from bizos.tools import base as tools_base
from bizos.tools.base import RunScope, guarded_call
from bizos.types import PolicyEffect, Role
from tests.test_unified_assistant import SCRIPT, _reset, acq, ask, call, fake_model, final  # noqa: F401

CAMPAIGN = ("update_selection", "show_contacts", "show_campaigns", "propose_email", "approve_move", "send_approved")


@pytest.fixture
def sends(monkeypatch):
    """Stub the hand-off to Instantly and record what would have been sent."""
    sent: list[dict] = []

    def fake_fulfill(ctx, payload):
        sent.append(payload)
        return agent.FulfillResult(campaign_id="cmp_test", message="Sent to 1 person in Fresh")

    monkeypatch.setattr(agent, "fulfill_approved_email", fake_fulfill)
    return sent


def _draft_for_nina(ctx, settings) -> str:
    """Turn 1: commit Nina, a new campaign and a window, and propose the email. Returns the session id."""
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Fresh",
             confirmed_new_campaign=True, window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm"),
        call("propose_email", subject="Hello", body="Hi Nina"),
        final("Here's the email. Approve it as is, or change something?"),
    ]
    first = ask(ctx, settings, "email Nina in a new campaign Fresh, 9 am to 5 pm")
    assert "Subject:" in first["trace"]["email_draft"], first["trace"]["tool_log"]
    return first["session_id"]


def _send_turn(ctx, settings, sid, message, evidence):
    SCRIPT[:] = [call("send_approved", approval_evidence=evidence), final("Done.")]
    out = ask(ctx, settings, message, session_id=sid)
    result = [c for c in out["trace"]["tool_log"] if c["tool"] == "send_approved"][0]["result"]
    return out, result


def _evidence_rows(ctx, status):
    with tenant_scope(ctx):
        return audit.search(ctx=ctx, tool="campaign_send_approved", status=status, limit=20)


# --------------------------------------------------------------------------- registration


def test_campaign_tools_are_registered_admin_only_and_in_no_pack():
    for name in CAMPAIGN:
        spec = get_spec(name)
        assert spec is not None, name
        assert spec.view_min_role == Role.ADMIN
        assert [r for r in Role if spec.can_run(r)] == [Role.ADMIN], name
        assert all(name not in {s.name for s in p.tool_specs()} for p in PACKS), name
    assert get_spec("send_approved").write and not get_spec("propose_email").write


@pytest.mark.parametrize("who", ["viewer", "drafter", "approver", "operator"])
def test_non_admins_are_denied_every_campaign_tool(client_scratch, ctx_factory, who):
    class Untouched:
        def call(self, *a, **k):  # pragma: no cover - must never run
            raise AssertionError("the policy check must stop a non-admin before the session runs")

    ctx = ctx_factory(client_scratch, who)
    scope = RunScope(ctx=ctx, settings=client_scratch.settings, domain="general", campaign_session=Untouched())
    with tenant_scope(ctx):
        for name in CAMPAIGN:
            outcome = guarded_call(scope, name, {"why": "probe"})
            assert outcome.effect == PolicyEffect.DENY and not outcome.ok, (who, name)


def test_campaign_effects_refuse_without_a_session(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    scope = RunScope(ctx=ctx, settings=client_scratch.settings, domain="general")
    with tenant_scope(ctx):
        outcome = guarded_call(scope, "show_contacts", {})
    assert not outcome.ok and "admin's chat turn" in outcome.message


def test_every_campaign_call_goes_through_the_policy_check(fake_model, acq, client_scratch, ctx_factory, monkeypatch):
    seen: list[str] = []
    real = tools_base.guarded_call

    def spy(scope, tool, arguments=None):
        seen.append(tool)
        return real(scope, tool, arguments)

    monkeypatch.setattr(tools_base, "guarded_call", spy)
    _draft_for_nina(ctx_factory(client_scratch, "admin"), client_scratch.settings)
    assert seen.count("update_selection") == 1 and seen.count("propose_email") == 1


# --------------------------------------------------------------------------- approval evidence


def test_a_real_quote_from_the_latest_message_sends_directly(fake_model, acq, client_scratch, ctx_factory, sends):
    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for_nina(ctx, client_scratch.settings)
    with tenant_scope(ctx):
        before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}
    out, result = _send_turn(ctx, client_scratch.settings, sid, "Looks great, SEND   it now", "send it")
    assert result.startswith("sent:"), result
    assert len(sends) == 1 and sends[0]["email_address_list"] == ["nina@acme.example.com"]
    assert out["trace"]["tool"] == "execute"
    # Same workflow as before: a WAIT_FOR_APPROVAL workspace does not queue an admin's campaign send.
    with tenant_scope(ctx):
        assert {a.id for a in actions.list_actions(limit=200, ctx=ctx)} == before
    logged = _evidence_rows(ctx, "ACTION_EXECUTED")
    assert any("send it" in str(r.get("result")) for r in logged)


def test_fabricated_evidence_is_denied(fake_model, acq, client_scratch, ctx_factory, sends):
    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for_nina(ctx, client_scratch.settings)
    _out, result = _send_turn(ctx, client_scratch.settings, sid, "hmm, let me think", "yes, send it")
    assert "approval_not_found" in result and "Ask the user" in result
    assert sends == []
    assert any("yes, send it" in str(r.get("result")) for r in _evidence_rows(ctx, "APPROVAL_EVIDENCE_NOT_FOUND"))


def test_evidence_quoted_from_an_older_message_is_denied(fake_model, acq, client_scratch, ctx_factory, sends):
    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for_nina(ctx, client_scratch.settings)
    # "email Nina" is the user's own words, but from turn 1, not the latest message.
    _out, result = _send_turn(ctx, client_scratch.settings, sid, "what time zone will it use?", "email Nina")
    assert "approval_not_found" in result
    assert sends == []


@pytest.mark.parametrize("evidence", ["", "   ", "s"])
def test_empty_or_partial_word_evidence_is_denied(fake_model, acq, client_scratch, ctx_factory, sends, evidence):
    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for_nina(ctx, client_scratch.settings)
    _out, result = _send_turn(ctx, client_scratch.settings, sid, "send it", evidence)
    assert "approval_not_found" in result
    assert sends == []


# --------------------------------------------------------------------------- no dead approval cards


def _queued_send_is_refused(ctx, draft_settings, send_settings, sends):
    sid = _draft_for_nina(ctx, draft_settings)
    with tenant_scope(ctx):
        before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}
    SCRIPT[:] = [call("send_approved", approval_evidence="send it"), final("I couldn't send it.")]
    out = ask(ctx, send_settings, "send it", session_id=sid)
    result = [c for c in out["trace"]["tool_log"] if c["tool"] == "send_approved"][0]["result"]
    assert "can't wait for approval" in result and "send it directly" in result, result
    assert sends == []
    assert out["trace"]["tool"] != "execute"
    with tenant_scope(ctx):
        assert {a.id for a in actions.list_actions(limit=200, ctx=ctx)} == before, "no approval card is created"
        assert audit.search(ctx=ctx, tool="send_approved", status="NOT_QUEUEABLE", limit=5)


def test_a_send_the_policy_would_queue_is_refused_instead(fake_model, acq, client_scratch, ctx_factory, sends):
    from bizos.control.models import ClientSettings

    strict = client_scratch.settings.to_dict()
    strict["risk_policy"] = {**strict["risk_policy"], "always_approve_at_or_above": "MEDIUM"}
    _queued_send_is_refused(
        ctx_factory(client_scratch, "admin"), client_scratch.settings, ClientSettings.from_dict(strict), sends
    )


def test_an_admin_override_requiring_approval_refuses_the_send(fake_model, acq, client_scratch, ctx_factory, sends):
    from bizos import admin_ops as ops

    ctx = ctx_factory(client_scratch, "admin")
    ops.set_tool_permission(ctx, tool="send_approved", role="ADMIN", permission="EXECUTE_AFTER_APPROVAL")
    try:
        _queued_send_is_refused(ctx, client_scratch.settings, client_scratch.settings, sends)
    finally:
        ops.clear_tool_permission(ctx, tool="send_approved", role="ADMIN")
