"""
§30 — the four execution modes, each asserted against real database state.

The point of every test here is the same: not that the API returned a particular
string, but that the world did or did not change.
"""

from __future__ import annotations

from sqlalchemy import text

from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import readonly_connection
from bizos.tools.base import guarded_call
from bizos.types import ActionStatus, ExecutionMode, PolicyEffect


def _counts(ctx) -> dict[str, int]:
    with readonly_connection(ctx) as conn:
        return {
            "drafts": conn.execute(text("SELECT count(*) FROM email_drafts")).scalar(),
            "sent": conn.execute(
                text("SELECT count(*) FROM email_messages WHERE direction='outbound'")
            ).scalar(),
            "events": conn.execute(
                text("SELECT count(*) FROM calendar_events WHERE status<>'cancelled'")
            ).scalar(),
        }


# --------------------------------------------------------------- MODE 1


def test_advise_allows_reads(client_a, scope_factory):
    scope = scope_factory(client_a, "operator", mode=ExecutionMode.ADVISE)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(scope, "crm_pipeline_summary", {})
    assert outcome.effect == PolicyEffect.ALLOW and outcome.ok


def test_advise_blocks_every_write(client_a, scope_factory):
    """"Update their CRM status" must be refused, and nothing may change."""
    scope = scope_factory(client_a, "operator", mode=ExecutionMode.ADVISE)
    with tenant_scope(scope.ctx):
        before = _counts(scope.ctx)
        for tool, args in (
            ("email_send_message", {"recipients": ["a@b.com"], "subject": "s", "body": "b"}),
            ("crm_update_contact_field", {"contact_id": "x", "field": "title", "value": "y"}),
            ("calendar_create_event", {"title": "t", "starts_at": "2026-10-01T10:00:00+00:00",
                                       "ends_at": "2026-10-01T11:00:00+00:00"}),
        ):
            outcome = guarded_call(scope, tool, args)
            assert outcome.effect == PolicyEffect.DENY, tool
            assert outcome.decision.rule == "MODE_ADVISE", tool
        assert _counts(scope.ctx) == before


def test_advise_read_tools_run_on_a_readonly_session(client_a, ctx_factory):
    """The substrate layer: the read connection cannot write even by mistake."""
    ctx = ctx_factory(client_a, "operator")
    with tenant_scope(ctx), readonly_connection(ctx) as conn:
        try:
            conn.execute(text("INSERT INTO crm_notes (id, body, author) VALUES ('x','y','z')"))
            raise AssertionError("a write succeeded on the read-only engine")
        except Exception as exc:
            assert "read-only" in str(exc).lower()


# --------------------------------------------------------------- MODE 2


def test_draft_creates_a_draft_and_sends_nothing(client_a, scope_factory):
    """§30 Draft Mode: a Drafter's "email John" produces a draft, nothing sent."""
    scope = scope_factory(client_a, "drafter", mode=ExecutionMode.DRAFT)
    with tenant_scope(scope.ctx):
        before = _counts(scope.ctx)
        outcome = guarded_call(
            scope, "email_send_message",
            {"recipients": ["john@northwind.com"], "subject": "Tomorrow's meeting",
             "body": "Hi John, confirming 10am."},
        )
        after = _counts(scope.ctx)

    assert outcome.effect == PolicyEffect.DRAFT_ONLY
    assert outcome.action_id is not None
    assert after["drafts"] == before["drafts"] + 1, "a draft should exist"
    assert after["sent"] == before["sent"], "NOTHING may be sent in draft mode"

    with tenant_scope(scope.ctx), readonly_connection(scope.ctx) as conn:
        row = conn.execute(
            text("SELECT recipients, subject, status FROM email_drafts ORDER BY created_at DESC LIMIT 1")
        ).first()
    assert row.status == "draft"
    assert row.recipients == ["john@northwind.com"]


def test_draft_records_the_action_as_draft(client_a, scope_factory):
    from bizos.actions import store as actions

    scope = scope_factory(client_a, "drafter", mode=ExecutionMode.DRAFT)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "email_send_message",
            {"recipients": ["x@example.com"], "subject": "s", "body": "b"},
        )
        action = actions.get_action(outcome.action_id, ctx=scope.ctx)
    assert action.status == ActionStatus.DRAFT


# --------------------------------------------------------------- MODE 3


def test_approval_mode_queues_and_changes_nothing(client_a, scope_factory):
    """§30 Approval Mode: the calendar stays untouched until someone approves."""
    from bizos.actions import store as actions

    scope = scope_factory(client_a, "operator", domain="planning",
                          mode=ExecutionMode.WAIT_FOR_APPROVAL)
    with tenant_scope(scope.ctx):
        before = _counts(scope.ctx)
        outcome = guarded_call(
            scope, "calendar_create_event",
            {"title": "Client meeting", "starts_at": "2026-11-02T14:00:00+00:00",
             "ends_at": "2026-11-02T15:00:00+00:00", "attendees": ["john@northwind.com"]},
        )
        after = _counts(scope.ctx)
        action = actions.get_action(outcome.action_id, ctx=scope.ctx)

    assert outcome.effect == PolicyEffect.REQUIRE_APPROVAL
    assert action.status == ActionStatus.PENDING_APPROVAL
    assert after["events"] == before["events"], "the calendar must be unchanged"


def test_approval_then_execution_creates_the_event(client_a, scope_factory, ctx_factory):
    """After an approver accepts, an operator's execution makes the change."""
    from bizos.actions import store as actions
    from bizos.tools.executor import execute_approved_action

    proposer = scope_factory(client_a, "operator", domain="planning",
                             mode=ExecutionMode.WAIT_FOR_APPROVAL)
    with tenant_scope(proposer.ctx):
        before = _counts(proposer.ctx)
        outcome = guarded_call(
            proposer, "calendar_create_event",
            {"title": "Approved meeting", "starts_at": "2026-11-03T14:00:00+00:00",
             "ends_at": "2026-11-03T15:00:00+00:00"},
        )
    action_id = outcome.action_id

    approver_ctx = ctx_factory(client_a, "approver")
    with tenant_scope(approver_ctx):
        approved = actions.approve(action_id, settings=client_a.settings,
                                   comment="ok", ctx=approver_ctx)
    assert approved.status == ActionStatus.APPROVED

    with tenant_scope(proposer.ctx):
        result = execute_approved_action(proposer, action_id)
        after = _counts(proposer.ctx)
        final = actions.get_action(action_id, include_events=True, ctx=proposer.ctx)

    assert result.ok
    assert final.status == ActionStatus.COMPLETED
    assert after["events"] == before["events"] + 1
    assert [e.event for e in final.events] == [
        "PROPOSED", "APPROVAL_REQUESTED", "APPROVED", "EXECUTION_STARTED", "EXECUTION_COMPLETED",
    ]


# --------------------------------------------------------------- MODE 4


def test_auto_executes_a_low_risk_action(client_a, scope_factory):
    """§30 Auto Mode: tagging a lead is low risk, so it happens immediately."""
    from bizos.audit import events as audit

    scope = scope_factory(client_a, "operator", domain="intake",
                          mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        with readonly_connection(scope.ctx) as conn:
            contact = conn.execute(
                text("SELECT id, tags FROM crm_contacts WHERE lifecycle='lead' LIMIT 1")
            ).first()
        outcome = guarded_call(
            scope, "crm_tag_lead",
            {"contact_id": contact.id, "add_tags": ["qualified"], "lifecycle": "qualified"},
        )
        with readonly_connection(scope.ctx) as conn:
            updated = conn.execute(
                text("SELECT tags, lifecycle FROM crm_contacts WHERE id=:i"), {"i": contact.id}
            ).first()
        trail = audit.search(ctx=scope.ctx, tool="crm_tag_lead", limit=5)

    assert outcome.effect == PolicyEffect.ALLOW and outcome.ok
    assert "qualified" in updated.tags and updated.lifecycle == "qualified"
    assert any(r["event_type"] == "ACTION_EXECUTED" for r in trail), "audit record required"


def test_auto_still_queues_above_the_risk_ceiling(client_a, scope_factory):
    """Auto is "within scope": a MEDIUM action above the ceiling still queues."""
    scope = scope_factory(client_a, "operator", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "crm_update_contact_field",
            {"contact_id": "whoever", "field": "title", "value": "VP"},
        )
    assert outcome.effect == PolicyEffect.REQUIRE_APPROVAL
    assert outcome.decision.rule == "AUTO_RISK_CEILING"


def test_a_request_may_narrow_the_mode_but_never_widen_it(client_a, scope_factory):
    """§5: a per-request override can only move toward more restriction."""
    # Workspace is WAIT_FOR_APPROVAL; a request asking for AUTO gets no more power.
    widening = scope_factory(client_a, "operator", mode=ExecutionMode.WAIT_FOR_APPROVAL,
                             requested_mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    assert widening.mode == ExecutionMode.WAIT_FOR_APPROVAL

    # Asking for ADVISE does narrow it.
    narrowing = scope_factory(client_a, "operator", mode=ExecutionMode.WAIT_FOR_APPROVAL,
                              requested_mode=ExecutionMode.ADVISE)
    assert narrowing.mode == ExecutionMode.ADVISE
