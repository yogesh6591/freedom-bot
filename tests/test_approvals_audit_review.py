"""Approval queue lifecycle, audit integrity and the human review queue."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from bizos.actions import store as actions
from bizos.audit import events as audit
from bizos.review import store as reviews
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import workspace_connection
from bizos.tools.base import guarded_call
from bizos.tools.executor import execute_approved_action
from bizos.types import ActionStatus, AuditEventType, ExecutionMode, ReviewStatus


def _propose(scope, **overrides):
    payload = {
        "title": "Queued meeting",
        "starts_at": "2026-12-01T10:00:00+00:00",
        "ends_at": "2026-12-01T11:00:00+00:00",
        **overrides,
    }
    return guarded_call(scope, "calendar_create_event", payload)


# ----------------------------------------------------------------- approvals


def test_full_lifecycle_is_recorded(client_a, scope_factory, ctx_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(proposer.ctx):
        action_id = _propose(proposer).action_id

    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        actions.approve(action_id, settings=client_a.settings, comment="fine", ctx=approver_ctx)
    with tenant_scope(proposer.ctx):
        execute_approved_action(proposer, action_id)
        final = actions.get_action(action_id, include_events=True, ctx=proposer.ctx)

    assert final.status == ActionStatus.COMPLETED
    events = [e.event for e in final.events]
    assert events == ["PROPOSED", "APPROVAL_REQUESTED", "APPROVED", "EXECUTION_STARTED",
                      "EXECUTION_COMPLETED"]
    assert final.approval.decided_by == approver_ctx.user_id
    assert final.approval.comment == "fine"


def test_self_approval_is_refused_when_disabled(client_a, scope_factory, ctx_factory):
    """The proposer cannot approve their own action when the policy forbids it."""
    from dataclasses import replace

    locked = replace(
        client_a.settings,
        approval_policy=replace(client_a.settings.approval_policy, allow_self_approval=False),
    )
    approver_ctx = ctx_factory(client_a, "approver")
    scope = scope_factory(client_a, "approver", domain="operations")
    with tenant_scope(approver_ctx):
        outcome = guarded_call(scope, "crm_create_task", {"title": "Self approval probe"})
        assert outcome.effect.value == "REQUIRE_APPROVAL"
        with pytest.raises(actions.SelfApprovalRejected):
            actions.approve(outcome.action_id, settings=locked, ctx=approver_ctx)


def test_self_approval_is_allowed_by_default(client_a, scope_factory, ctx_factory):
    """Demo default: proposer may approve their own action when policy allows it."""
    approver_ctx = ctx_factory(client_a, "approver")
    scope = scope_factory(client_a, "approver", domain="operations")
    with tenant_scope(approver_ctx):
        outcome = guarded_call(scope, "crm_create_task", {"title": "Self approval allowed"})
        assert outcome.effect.value == "REQUIRE_APPROVAL"
        approved = actions.approve(
            outcome.action_id, settings=client_a.settings, comment="self ok", ctx=approver_ctx
        )
        assert approved.status == ActionStatus.APPROVED


def test_a_non_approver_cannot_decide(client_a, scope_factory, ctx_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(proposer.ctx):
        action_id = _propose(proposer, title="Non-approver probe").action_id
    for role in ("viewer", "drafter", "operator"):
        ctx = ctx_factory(client_a, role)
        with tenant_scope(ctx):
            with pytest.raises(actions.NotAnApprover):
                actions.approve(action_id, settings=client_a.settings, ctx=ctx)


def test_rejected_actions_cannot_be_executed(client_a, scope_factory, ctx_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(proposer.ctx):
        action_id = _propose(proposer, title="Rejected probe").action_id
    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        actions.reject(action_id, settings=client_a.settings, comment="no", ctx=approver_ctx)
    with tenant_scope(proposer.ctx):
        from bizos.tools.executor import NotExecutable

        with pytest.raises(NotExecutable):
            execute_approved_action(proposer, action_id)


def test_illegal_transitions_are_refused(client_a, scope_factory):
    """An action cannot jump from PENDING_APPROVAL straight to COMPLETED."""
    scope = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(scope.ctx):
        action_id = _propose(scope, title="Transition probe").action_id
        with pytest.raises(actions.IllegalTransition):
            actions.mark_completed(action_id, {"forced": True}, ctx=scope.ctx)


def test_request_changes_returns_it_to_draft(client_a, scope_factory, ctx_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(proposer.ctx):
        action_id = _propose(proposer, title="Changes probe").action_id
    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        result = actions.request_changes(
            action_id, settings=client_a.settings, comment="wrong time", ctx=approver_ctx
        )
    assert result.status == ActionStatus.DRAFT


def test_edit_before_approval_records_both_payloads(client_a, scope_factory, ctx_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(proposer.ctx):
        outcome = _propose(proposer, title="Edit probe")
    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        edited = actions.edit_payload(
            outcome.action_id,
            {
                "title": "Edited title",
                "starts_at": "2026-12-02T10:00:00+00:00",
                "ends_at": "2026-12-02T11:00:00+00:00",
            },
            settings=client_a.settings, comment="fixed the time", ctx=approver_ctx,
        )
        detail = actions.get_action(outcome.action_id, include_events=True, ctx=approver_ctx)
    assert edited.payload["title"] == "Edited title"
    edit_event = next(e for e in detail.events if e.event == "PAYLOAD_EDITED")
    assert edit_event.payload_before["title"] == "Edit probe"
    assert edit_event.payload_after["title"] == "Edited title"


def test_approval_does_not_unlock_a_denial(client_a, scope_factory, ctx_factory, settings_with):
    """Policy runs again at execution: a rule tightened after approval still holds."""
    proposer = scope_factory(client_a, "operator", domain="planning")
    with tenant_scope(proposer.ctx):
        action_id = _propose(proposer, title="Tightened probe").action_id
    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        actions.approve(action_id, settings=client_a.settings, ctx=approver_ctx)

    # The admin then disables the planning domain entirely.
    tightened = settings_with(client_a.settings, enabled_domains=["general"])
    strict = scope_factory(client_a, "operator", domain="planning", settings=tightened)
    with tenant_scope(strict.ctx):
        result = execute_approved_action(strict, action_id)
        final = actions.get_action(action_id, ctx=strict.ctx)
    assert not result.ok
    assert final.status == ActionStatus.FAILED
    assert "domain" in (final.error or "").lower()


# --------------------------------------------------------------------- audit


def test_audit_is_append_only(client_a, ctx_factory):
    """§9: a database trigger rejects UPDATE and DELETE, even from the owner."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        audit.log(AuditEventType.LOGIN, ctx=ctx, status="OK", request={"probe": True})
        with pytest.raises(Exception) as update_error:
            with workspace_connection(ctx) as conn:
                conn.execute(text("UPDATE audit_events SET status='TAMPERED'"))
        with pytest.raises(Exception) as delete_error:
            with workspace_connection(ctx) as conn:
                conn.execute(text("DELETE FROM audit_events"))
    assert "append-only" in str(update_error.value)
    assert "append-only" in str(delete_error.value)


def test_audit_redacts_secrets(client_a, ctx_factory):
    """§9: no password, token, key or card number reaches a row."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        event_id = audit.log(
            AuditEventType.TOOL_CALL, ctx=ctx, tool="probe",
            request={
                "password": "hunter2",
                "refresh_token": "1//0abcdefghijklmnop",
                "api_key": "sk-abcdefghijklmnopqrst",
                "note": "card 4111 1111 1111 1111 and Bearer eyJhbGciOi.eyJzdWIi.SflKxwRJ",
                "db": "postgresql://user:secret@host/db",
                "safe": "ordinary business text",
            },
        )
        with workspace_connection(ctx) as conn:
            row = conn.execute(
                text("SELECT request::text AS body FROM audit_events WHERE id = :i"),
                {"i": event_id},
            ).first()
    body = row.body
    for secret in ("hunter2", "1//0abcdefghijklmnop", "sk-abcdefghijklmnopqrst",
                   "4111 1111 1111 1111", "eyJhbGciOi.eyJzdWIi.SflKxwRJ", "user:secret@host"):
        assert secret not in body, f"{secret!r} leaked into the audit log"
    assert "ordinary business text" in body


def test_every_policy_decision_is_audited(client_a, scope_factory):
    """§9: decisions are recorded whether they allowed or refused."""
    scope = scope_factory(client_a, "viewer", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        before = len(audit.search(ctx=scope.ctx, event_types=["POLICY_DECISION"], limit=500))
        guarded_call(scope, "email_send_message",
                     {"recipients": ["a@b.com"], "subject": "s", "body": "b"})
        rows = audit.search(ctx=scope.ctx, event_types=["POLICY_DECISION"], limit=500)
    assert len(rows) > before
    assert rows[0]["decision"] == "DENY"
    assert rows[0]["user_id"] == scope.ctx.user_id


def test_audit_search_filters(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        rows = audit.search(ctx=ctx, event_types=["POLICY_DECISION"], limit=10)
    assert all(r["event_type"] == "POLICY_DECISION" for r in rows)


# -------------------------------------------------------------- human review


def test_human_review_lifecycle(client_a, scope_factory, ctx_factory):
    """§16: raise, list, resolve, and read the answer back."""
    scope = scope_factory(client_a, "operator", domain="intake")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "request_human_review",
            {
                "question": "Which project does this task belong to?",
                "context": "Two projects match the description.",
                "choices": ["Northwind rollout", "Contoso pilot"],
                "recommended_option": "Northwind rollout",
                "confidence": 0.4,
            },
        )
        review_id = outcome.data["review_id"]
        item = reviews.get(review_id, ctx=scope.ctx)
        assert item.status == ReviewStatus.OPEN
        assert item.choices == ["Northwind rollout", "Contoso pilot"]
        assert item.confidence == 0.4

        assert reviews.resolution_for(review_id, ctx=scope.ctx) is None
        reviews.resolve(review_id, resolution="Contoso pilot", note="checked with ops",
                        ctx=scope.ctx)
        assert reviews.resolution_for(review_id, ctx=scope.ctx) == "Contoso pilot"
        resolved = reviews.get(review_id, ctx=scope.ctx)

    assert resolved.status == ReviewStatus.RESOLVED
    assert resolved.resolved_by == scope.ctx.user_id


def test_resolving_twice_is_refused(client_a, scope_factory):
    scope = scope_factory(client_a, "operator", domain="intake")
    with tenant_scope(scope.ctx):
        review = reviews.create(question="Double resolve probe?", ctx=scope.ctx)
        reviews.resolve(review.id, resolution="yes", ctx=scope.ctx)
        with pytest.raises(reviews.ReviewNotFound):
            reviews.resolve(review.id, resolution="again", ctx=scope.ctx)
