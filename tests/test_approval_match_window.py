"""Approval `match` looks at every open request, not only the newest 200.

A phrase that matches one newer request and one older request (beyond the first 200) is ambiguous and
must be refused, with nothing approved.
"""

from __future__ import annotations

from app.assistant.company_tools import _MATCH_PAGE, _resolve
from bizos.actions import store as actions
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope, guarded_call
from bizos.types import ActionStatus, PolicyEffect


def _queue(ctx, settings, decision, title):
    return actions.create_action(
        title=title, description=title, explanation="test", tool="crm_create_note",
        payload={"contact_id": "C-1", "body": title}, decision=decision, settings=settings,
        status=ActionStatus.PENDING_APPROVAL, domain="operations", ctx=ctx,
    )


def _queue_with_filler(client, drafter, older_title, newer_title):
    with tenant_scope(drafter):
        decision = evaluate(PolicyRequest(ctx=drafter, settings=client.settings, tool="crm_create_note",
                                          domain="operations", connected_integrations=frozenset({"crm"})))
        assert decision.effect == PolicyEffect.REQUIRE_APPROVAL, (decision.rule, decision.reason)
        older = _queue(drafter, client.settings, decision, older_title)
        for i in range(_MATCH_PAGE):
            _queue(drafter, client.settings, decision, f"Follow-up note {i}")
        newer = _queue(drafter, client.settings, decision, newer_title) if newer_title else None
    return older, newer


def test_a_phrase_matching_a_newer_and_an_older_request_is_refused(client_scratch, ctx_factory):
    drafter = ctx_factory(client_scratch, "drafter")
    approver = ctx_factory(client_scratch, "approver")
    older, newer = _queue_with_filler(client_scratch, drafter, "Meena expense claim, September",
                                       "Meena expense claim, October")

    with tenant_scope(approver):
        out = guarded_call(RunScope(ctx=approver, settings=client_scratch.settings, domain="operations"),
                           "approvals_approve", {"match": "Meena expense"})
        assert not out.ok
        assert "More than one action matches" in out.message
        assert older.id in out.message and newer.id in out.message
        for action in (older, newer):
            assert actions.get_action(action.id, ctx=approver).status == ActionStatus.PENDING_APPROVAL


def test_a_phrase_matching_only_an_older_request_finds_it(client_scratch, ctx_factory):
    drafter = ctx_factory(client_scratch, "drafter")
    approver = ctx_factory(client_scratch, "approver")
    older, _ = _queue_with_filler(client_scratch, drafter, "Ravi travel advance, August", None)

    with tenant_scope(approver):
        scope = RunScope(ctx=approver, settings=client_scratch.settings, domain="operations")
        found, problem = _resolve(scope, {"match": "Ravi travel"}, [ActionStatus.PENDING_APPROVAL])
    assert problem is None and found is not None and found.id == older.id
