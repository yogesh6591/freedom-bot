"""Working the approval queue from chat, under the caller's own authority."""

from __future__ import annotations

from dataclasses import replace

from bizos.actions import store as actions
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import guarded_call
from bizos.types import ActionStatus, ExecutionMode, PolicyEffect


def _propose(scope, title: str) -> str:
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "calendar_create_event",
            {
                "title": title,
                "starts_at": "2026-12-03T10:00:00+00:00",
                "ends_at": "2026-12-03T11:00:00+00:00",
            },
        )
    assert outcome.action_id, outcome.message
    return outcome.action_id


def _call(scope, tool: str, **args):
    with tenant_scope(scope.ctx):
        return guarded_call(scope, tool, args)


def _status(scope, action_id: str) -> ActionStatus:
    with tenant_scope(scope.ctx):
        return actions.get_action(action_id, ctx=scope.ctx).status


def test_viewer_and_drafter_cannot_decide(client_a, scope_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    action_id = _propose(proposer, "Chat decide probe")
    for role in ("viewer", "drafter"):
        scope = scope_factory(client_a, role, domain="general")
        for tool in ("approvals_approve", "approvals_reject"):
            outcome = _call(scope, tool, action_id=action_id)
            assert outcome.effect == PolicyEffect.DENY, (role, tool)
    assert _status(proposer, action_id) == ActionStatus.PENDING_APPROVAL


def test_operator_without_approver_role_is_told_why(client_a, scope_factory):
    proposer = scope_factory(client_a, "approver", domain="planning")
    action_id = _propose(proposer, "Operator decide probe")
    operator = scope_factory(client_a, "operator", domain="general")
    outcome = _call(operator, "approvals_approve", action_id=action_id)
    assert not outcome.ok
    assert "Not permitted" in outcome.message and "APPROVER" in outcome.message
    assert _status(proposer, action_id) == ActionStatus.PENDING_APPROVAL


def test_approver_approves_and_it_runs_without_queueing_itself(client_a, scope_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    action_id = _propose(proposer, "Chat approve probe")
    approver = scope_factory(client_a, "approver", domain="general")
    assert approver.mode == ExecutionMode.WAIT_FOR_APPROVAL
    with tenant_scope(approver.ctx):
        before = {a.id for a in actions.list_actions(limit=500, ctx=approver.ctx)}
    outcome = _call(approver, "approvals_approve", action_id=action_id, comment="looks right")
    assert outcome.ok and outcome.effect == PolicyEffect.ALLOW, outcome.message
    assert outcome.data["executed"] is True
    assert _status(proposer, action_id) == ActionStatus.COMPLETED
    with tenant_scope(approver.ctx):
        after = {a.id for a in actions.list_actions(limit=500, ctx=approver.ctx)}
    assert after == before

    listed = _call(approver, "approvals_list", status="completed")
    assert action_id in {a["action_id"] for a in listed.data["actions"]}
    detail = _call(approver, "approvals_get", action_id=action_id)
    events = [h["event"] for h in detail.data["history"]]
    assert "APPROVED" in events and "EXECUTION_COMPLETED" in events
    approved = next(h for h in detail.data["history"] if h["event"] == "APPROVED")
    assert approved["comment"] == "looks right"


def test_self_approval_is_refused_from_chat_when_disabled(client_a, scope_factory):
    locked = replace(
        client_a.settings,
        approval_policy=replace(client_a.settings.approval_policy, allow_self_approval=False),
    )
    proposer = scope_factory(client_a, "approver", domain="planning", settings=locked)
    action_id = _propose(proposer, "Chat self approval probe")
    approver = scope_factory(client_a, "approver", domain="general", settings=locked)
    outcome = _call(approver, "approvals_approve", action_id=action_id)
    assert not outcome.ok and "yourself" in outcome.message
    assert _status(proposer, action_id) == ActionStatus.PENDING_APPROVAL


def test_reject_by_title_and_find_it_under_rejected(client_a, scope_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    action_id = _propose(proposer, "Quarterly zebra offsite")
    approver = scope_factory(client_a, "approver", domain="general")
    outcome = _call(approver, "approvals_reject", match="zebra offsite", comment="not this quarter")
    assert outcome.ok, outcome.message
    assert _status(proposer, action_id) == ActionStatus.REJECTED

    rejected = _call(approver, "approvals_list", status="rejected")
    row = next(a for a in rejected.data["actions"] if a["action_id"] == action_id)
    assert row["decision_comment"] == "not this quarter"
    again = _call(approver, "approvals_approve", action_id=action_id)
    assert not again.ok and "REJECTED" in again.message


def test_drafter_submits_own_draft_but_not_someone_elses(client_a, scope_factory):
    drafter = scope_factory(client_a, "drafter", domain="planning")
    action_id = _propose(drafter, "Drafter chat submit probe")
    assert _status(drafter, action_id) == ActionStatus.DRAFT

    other = scope_factory(client_a, "operator", domain="general")
    refused = _call(other, "approvals_submit", action_id=action_id)
    assert not refused.ok and "someone else's draft" in refused.message

    mine = _call(scope_factory(client_a, "drafter", domain="general"), "approvals_list", status="drafts", mine=True)
    assert action_id in {a["action_id"] for a in mine.data["actions"]}
    submitted = _call(scope_factory(client_a, "drafter", domain="general"), "approvals_submit", action_id=action_id)
    assert submitted.ok, submitted.message
    assert _status(drafter, action_id) == ActionStatus.PENDING_APPROVAL


def test_requester_can_cancel_but_viewer_cannot(client_a, scope_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    action_id = _propose(proposer, "Chat cancel probe")
    viewer = scope_factory(client_a, "viewer", domain="general")
    assert _call(viewer, "approvals_cancel", action_id=action_id).effect == PolicyEffect.DENY
    cancelled = _call(scope_factory(client_a, "operator", domain="general"), "approvals_cancel", action_id=action_id)
    assert cancelled.ok, cancelled.message
    assert _status(proposer, action_id) == ActionStatus.CANCELLED


def test_advise_mode_forbids_decisions_from_chat(client_a, scope_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    action_id = _propose(proposer, "Advise decide probe")
    approver = scope_factory(client_a, "approver", domain="general", mode=ExecutionMode.ADVISE)
    assert _call(approver, "approvals_approve", action_id=action_id).effect == PolicyEffect.DENY
    assert _call(approver, "approvals_list", status="pending").ok
    assert _status(proposer, action_id) == ActionStatus.PENDING_APPROVAL


def test_submit_reply_is_not_stripped_as_a_false_claim():
    from bizos.agents.claims import enforce_claims

    reply = "Done — the draft has been submitted for approval."
    text, changed = enforce_claims(
        reply,
        message="submit my draft for approval",
        activity=[{"tool": "approvals_submit", "ok": True}],
        new_actions=[],
    )
    assert not changed and text == reply
