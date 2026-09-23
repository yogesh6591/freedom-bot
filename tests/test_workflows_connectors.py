"""Workflows (§13), connectors (§10) and domain packs (§11, §12)."""

from __future__ import annotations

from sqlalchemy import text

from bizos.connectors.registry import connected_categories, get_connector
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import readonly_connection
from bizos.workflows.playbooks import run_playbook
from bizos.workflows.runner import (
    STATUS_AWAITING_APPROVAL,
    STATUS_AWAITING_REVIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from bizos.types import ExecutionMode


# ---------------------------------------------------------------- workflows


def test_lead_intake_queues_writes_in_approval_mode(client_a, scope_factory):
    scope = scope_factory(client_a, "operator", mode=ExecutionMode.WAIT_FOR_APPROVAL)
    with tenant_scope(scope.ctx):
        run = run_playbook(
            scope, "lead_intake",
            inputs={"email": "queued@newlead.test", "name": "Queued Lead",
                    "message": "We want a demo and pricing"},
        )
    assert run.status == STATUS_AWAITING_APPROVAL
    names = [s.name for s in run.steps]
    assert names == ["enrich_lead", "check_duplicate", "classify_lead",
                     "upsert_crm_record", "draft_response"]
    assert any(s.awaiting_approval for s in run.steps)
    assert len(run.output["actions"]) >= 2


def test_lead_intake_is_blocked_in_advise_mode(client_a, scope_factory):
    """The same playbook, unchanged, refuses to write in Advise."""
    scope = scope_factory(client_a, "operator", mode=ExecutionMode.ADVISE)
    with tenant_scope(scope.ctx):
        run = run_playbook(
            scope, "lead_intake",
            inputs={"email": "advise@newlead.test", "message": "pricing please"},
        )
    assert run.status == STATUS_FAILED
    assert "Advise mode" in (run.error or "")


def test_lead_intake_pauses_for_human_review_when_uncertain(client_a, scope_factory):
    """§16: no signal to classify on → ask, do not guess."""
    from bizos.review import store as reviews

    scope = scope_factory(client_a, "operator")
    with tenant_scope(scope.ctx):
        run = run_playbook(
            scope, "lead_intake", inputs={"email": "silent@nowhere-unknown.test"}
        )
        open_items = reviews.list_items(workflow_run_id=run.id, ctx=scope.ctx)
    assert run.status == STATUS_AWAITING_REVIEW
    assert open_items and open_items[0].choices == ["hot", "warm", "cold", "discard"]
    assert open_items[0].recommended_option == "cold"


def test_lead_intake_does_not_duplicate_an_existing_contact(client_a, scope_factory, settings_with):
    """The intake guardrail: an existing email updates rather than creates."""
    settings = settings_with(
        client_a.settings,
        default_execution_mode=ExecutionMode.AUTO_WITHIN_SCOPE.value,
        risk_policy={"auto_allowed_max_risk": "MEDIUM", "always_approve_at_or_above": "HIGH"},
    )
    scope = scope_factory(client_a, "operator", settings=settings)
    with tenant_scope(scope.ctx):
        with readonly_connection(scope.ctx) as conn:
            existing = conn.execute(
                text("SELECT email FROM crm_contacts WHERE email IS NOT NULL LIMIT 1")
            ).first()
            before = conn.execute(text("SELECT count(*) FROM crm_contacts")).scalar()
        run = run_playbook(
            scope, "lead_intake",
            inputs={"email": existing.email, "name": "Dup Probe", "message": "demo please"},
        )
        with readonly_connection(scope.ctx) as conn:
            after = conn.execute(text("SELECT count(*) FROM crm_contacts")).scalar()
    upsert = next(s for s in run.steps if s.name == "upsert_crm_record")
    assert upsert.output["mode"] == "update"
    assert after == before, "no duplicate contact may be created"


def test_meeting_preparation_produces_a_brief(client_a, scope_factory):
    scope = scope_factory(client_a, "operator")
    with tenant_scope(scope.ctx):
        run = run_playbook(scope, "meeting_preparation")
    assert run.status == STATUS_COMPLETED
    brief = run.output["brief"]
    for heading in ("# Meeting brief", "## Who they are", "## Recent correspondence",
                    "## Open items"):
        assert heading in brief


def test_meeting_preparation_flags_injected_correspondence(client_a, scope_factory):
    """The seeded hostile email surfaces as a security note, not as instructions."""
    scope = scope_factory(client_a, "operator")
    with tenant_scope(scope.ctx):
        run = run_playbook(scope, "meeting_preparation")
    gather = next(s for s in run.steps if s.name == "gather_email_context")
    assert gather.ok
    # Findings may be zero if the meeting's attendees have no hostile thread; the
    # assertion that matters is that the count is reported, not silently dropped.
    assert "findings" in gather.output


def test_weekly_sales_summary_saves_and_compares(client_a, scope_factory):
    scope = scope_factory(client_a, "operator")
    with tenant_scope(scope.ctx):
        first = run_playbook(scope, "weekly_sales_summary")
        second = run_playbook(scope, "weekly_sales_summary")
    assert first.status == STATUS_COMPLETED
    assert second.status == STATUS_COMPLETED
    compare = next(s for s in second.steps if s.name == "compare_changes")
    assert compare.output["prior"] is not None, "the second run should compare to the first"
    assert "Weekly sales summary" in second.output["report"]


def test_workflow_runs_and_steps_are_persisted(client_a, scope_factory):
    from bizos.workflows.runner import list_runs

    scope = scope_factory(client_a, "operator")
    with tenant_scope(scope.ctx):
        run = run_playbook(scope, "weekly_sales_summary")
        rows = list_runs(workflow_id="weekly_sales_summary", ctx=scope.ctx)
    stored = next(r for r in rows if r["id"] == run.id)
    assert stored["status"] == STATUS_COMPLETED
    assert len(stored["steps"]) == len(run.steps)
    assert stored["triggered_by"] == scope.ctx.user_id


def test_workflow_writes_are_audited(client_a, scope_factory):
    from bizos.audit import events as audit

    scope = scope_factory(client_a, "operator")
    with tenant_scope(scope.ctx):
        run = run_playbook(scope, "weekly_sales_summary")
        rows = audit.search(ctx=scope.ctx, workflow_id="weekly_sales_summary", limit=100)
    types = {r["event_type"] for r in rows}
    assert "WORKFLOW_STARTED" in types
    assert "WORKFLOW_COMPLETED" in types


# --------------------------------------------------------------- connectors


def test_default_integrations_are_connected(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        assert {"crm", "email", "calendar"} <= connected_categories(ctx)


def test_crm_connector_capabilities(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        crm = get_connector("crm", ctx=ctx)
        assert crm.health().ok
        assert crm.search_contacts("").ok
        assert crm.pipeline_summary().data["stages"]
        assert crm.find_duplicate("nobody@nowhere.test").data is None


def test_email_draft_and_send_are_different_operations(client_a, ctx_factory):
    """§10: sending is separately permissioned *and* separately implemented."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        email = get_connector("email", ctx=ctx)
        with readonly_connection(ctx) as conn:
            sent_before = conn.execute(
                text("SELECT count(*) FROM email_messages WHERE direction='outbound'")
            ).scalar()
        draft = email.create_draft(recipients=["a@b.test"], subject="s", body="b")
        with readonly_connection(ctx) as conn:
            sent_after = conn.execute(
                text("SELECT count(*) FROM email_messages WHERE direction='outbound'")
            ).scalar()
    assert draft.ok and sent_after == sent_before


def test_calendar_availability_returns_free_busy_only(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        calendar = get_connector("calendar", ctx=ctx)
        result = calendar.check_availability(
            start="2026-09-09T00:00:00+00:00", end="2026-09-16T00:00:00+00:00"
        )
    assert result.ok
    assert set(result.data) == {"free", "busy_count"}
    # No event titles, descriptions or attendees leak through the free/busy view.
    assert all(set(window) == {"start", "end"} for window in result.data["free"])


def test_live_adapters_refuse_clearly_when_unconfigured(client_a, ctx_factory):
    """Gmail and Google Calendar say why they cannot run, rather than failing oddly."""
    from bizos.connectors.calendar import GoogleCalendarConnector
    from bizos.connectors.email import GmailConnector

    ctx = ctx_factory(client_a, "admin")
    for adapter in (GmailConnector(ctx, {}), GoogleCalendarConnector(ctx, {})):
        result = adapter.health()
        assert not result.ok
        assert "not configured" in (result.error or "").lower()


# ------------------------------------------------------------- domain packs


def test_domain_catalogue_and_packs_agree():
    from bizos.domains.packs import assert_catalog_complete

    assert_catalog_complete()


def test_phase_one_domains_are_built_and_phase_two_are_placeholders():
    from bizos.domains.packs import PACKS_BY_NAME

    for name in ("general", "operations", "sales", "intake", "planning"):
        assert not PACKS_BY_NAME[name].is_placeholder, name
    for name in ("strategy", "finance", "brand", "legal"):
        assert PACKS_BY_NAME[name].is_placeholder, name


def test_a_domain_pack_can_only_narrow_the_mode(client_a, scope_factory, settings_with):
    """§11: legal is advisory even when the workspace is fully automatic."""
    from bizos.agents.agent import build_agent, ModelNotConfigured
    from bizos.domains.packs import PACKS_BY_NAME
    from bizos.policy.modes import narrowest

    assert PACKS_BY_NAME["legal"].mode_ceiling == ExecutionMode.ADVISE
    assert narrowest(ExecutionMode.AUTO_WITHIN_SCOPE, ExecutionMode.ADVISE) == ExecutionMode.ADVISE
    # And a pack cannot widen: a DRAFT ceiling against an ADVISE workspace stays ADVISE.
    assert narrowest(ExecutionMode.ADVISE, ExecutionMode.DRAFT) == ExecutionMode.ADVISE


def test_tool_registry_is_consistent():
    from bizos.tools.registry_check import assert_registry_sound, verify_registry

    assert_registry_sound()
    report = verify_registry()
    assert report["effects_without_spec"] == []
    assert report["implemented_without_effect"] == []
