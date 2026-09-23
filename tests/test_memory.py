"""§30 — memory correction, versioning and provenance."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from bizos.memory import store as memory
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import workspace_connection
from bizos.tools.base import guarded_call
from bizos.types import (
    ApprovalStatus,
    ExecutionMode,
    MemoryCategory,
    MemoryStatus,
    SourceType,
)


def test_correction_supersedes_without_losing_history(client_a, ctx_factory):
    """§30: refund period 30 days → 14 days; 30 must survive as superseded."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.FACT, memory_key="refund_period_test",
            title="Refund period", content="Refund period = 30 days",
            settings=client_a.settings, ctx=ctx,
        )
        memory.correct(
            item_id=item.id, new_content="Refund period is now 14 days",
            reason="Policy change", settings=client_a.settings, ctx=ctx,
        )
        history = memory.history(item.id, ctx=ctx)
        # Search by this item's own key: the workspace also has a separate,
        # unrelated `refund_period` fact seeded with a 30-day value, and that
        # one is legitimately still current.
        hits = memory.search("refund_period_test", ctx=ctx)

    active = [v for v in history.versions if v.status == MemoryStatus.ACTIVE]
    superseded = [v for v in history.versions if v.status == MemoryStatus.SUPERSEDED]

    assert len(active) == 1
    assert "14 days" in active[0].content
    assert len(superseded) == 1
    assert "30 days" in superseded[0].content
    assert superseded[0].effective_until is not None
    assert superseded[0].superseded_by == active[0].id
    assert active[0].supersedes == superseded[0].id
    assert active[0].correction_reason == "Policy change"
    assert active[0].corrected_by == ctx.user_id
    assert active[0].attributes["previous_value"] == "Refund period = 30 days"

    # Retrieval of *this* item returns 14, never 30.
    contents = [i.current.content for i in hits if i.current]
    assert contents == ["Refund period is now 14 days"], contents


def test_renewal_cycle_correction_from_the_brief(client_a, ctx_factory):
    """§2 D worked example: 30 days superseded, 45 days active."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.FACT, memory_key="renewal_cycle_test",
            title="Customer renewal cycle", content="Customer renewal cycle = 30 days",
            settings=client_a.settings, ctx=ctx,
        )
        memory.correct(
            item_id=item.id, new_content="Customer renewal cycle = 45 days",
            reason="User correction", settings=client_a.settings, ctx=ctx,
        )
        history = memory.history(item.id, ctx=ctx)

    by_version = {v.version_no: v for v in history.versions}
    assert by_version[1].status == MemoryStatus.SUPERSEDED and "30" in by_version[1].content
    assert by_version[2].status == MemoryStatus.ACTIVE and "45" in by_version[2].content


def test_only_one_active_version_can_exist(client_a, ctx_factory):
    """The invariant is enforced by the database, not only by application code."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.FACT, memory_key="invariant_test",
            title="Invariant", content="first", settings=client_a.settings, ctx=ctx,
        )
        with pytest.raises(Exception) as excinfo:
            with workspace_connection(ctx) as conn:
                conn.execute(
                    text(
                        "INSERT INTO memory_versions (id, item_id, version_no, content, "
                        "created_by, status) VALUES ('mv_dupe', :i, 99, 'sneaky', 'x', 'ACTIVE')"
                    ),
                    {"i": item.id},
                )
    assert "memory_versions_one_active" in str(excinfo.value)


def test_ai_inferred_memory_is_not_trusted_equally(client_a, ctx_factory):
    """§3: inference lands PENDING and is excluded from authoritative retrieval."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.FACT, memory_key="inferred_test",
            title="Inferred preference", content="They probably prefer email",
            source_type=SourceType.AI_INFERENCE, confidence=0.55,
            settings=client_a.settings, ctx=ctx,
        )
        authoritative = memory.search("inferred preference", ctx=ctx)
        everything = memory.search("inferred preference", authoritative_only=False, ctx=ctx)

    assert item.current.approval_status == ApprovalStatus.PENDING
    assert item.current.is_authoritative is False
    assert authoritative == []
    assert len(everything) == 1

    with tenant_scope(ctx):
        memory.approve_version(item.current.id, ctx=ctx)
        after = memory.search("inferred preference", ctx=ctx)
    assert len(after) == 1


def test_manual_memory_outranks_inference(client_a, ctx_factory):
    """Source trust orders retrieval when both could answer."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        memory.put(
            category=MemoryCategory.FACT, memory_key="rank_manual",
            title="Support hours ranked", content="Support hours are 9-5 (stated)",
            source_type=SourceType.MANUAL, settings=client_a.settings, ctx=ctx,
        )
        inferred = memory.put(
            category=MemoryCategory.FACT, memory_key="rank_inferred",
            title="Support hours ranked guess", content="Support hours are 8-8 (guessed)",
            source_type=SourceType.AI_INFERENCE, confidence=0.9,
            settings=client_a.settings, ctx=ctx,
        )
        memory.approve_version(inferred.current.id, ctx=ctx)
        results = memory.search("support hours ranked", ctx=ctx)

    assert results, "both should be retrievable"
    assert results[0].current.source_type == SourceType.MANUAL


def test_provenance_is_recorded_on_every_version(client_a, ctx_factory):
    """§3's full metadata set is present."""
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.SOP, memory_key="provenance_test",
            title="Escalation SOP", content="Escalate to the duty manager after 2 hours.",
            source_type=SourceType.DOCUMENT, source_id="handbook.pdf#p12",
            confidence=0.95, settings=client_a.settings, ctx=ctx,
        )
    version = item.current
    assert version.source_type == SourceType.DOCUMENT
    assert version.source_id == "handbook.pdf#p12"
    assert version.created_by == ctx.user_id
    assert version.confidence == 0.95
    assert version.approval_status == ApprovalStatus.APPROVED
    assert version.effective_from is not None
    assert version.effective_until is None
    assert version.superseded_by is None


def test_decision_records_structured_fields(client_a, scope_factory):
    """§2 C: decision, date, approver, context, reason and status."""
    scope = scope_factory(client_a, "operator", domain="operations")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "memory_record_decision",
            {
                "title": "Adopt the new pricing tiers",
                "key": "decision_pricing_tiers",
                "content": "Adopt three pricing tiers from Q4.",
                "decision": "Adopt three pricing tiers from Q4.",
                "decided_on": "2026-09-01",
                "approved_by": "CFO",
                "context": "Margin review",
                "reason": "Simplifies the quote process",
                "status": "active",
            },
        )
    assert outcome.ok
    attributes = outcome.data["attributes"]
    assert attributes["approved_by"] == "CFO"
    assert attributes["decided_on"] == "2026-09-01"
    assert attributes["status"] == "active"


def test_correcting_by_key_when_nothing_exists_is_refused(client_a, scope_factory):
    """The agent is told to record a new fact rather than silently creating one."""
    scope = scope_factory(client_a, "operator", domain="operations")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "memory_correct",
            {"key": "does_not_exist_anywhere", "new_content": "x", "reason": "y"},
        )
    assert not outcome.ok
    assert "nothing to correct" in outcome.message.lower()


def test_memory_writes_are_available_in_draft_mode(client_a, scope_factory):
    """Recording what the user just said is not a business action to queue."""
    scope = scope_factory(client_a, "drafter", domain="operations", mode=ExecutionMode.DRAFT)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "memory_add_fact",
            {"key": "draft_mode_fact", "title": "Draft-mode fact", "content": "Recorded in draft mode"},
        )
    assert outcome.ok, outcome.message


def test_memory_add_fact_rejects_empty_content(client_a, scope_factory):
    """Blank writes must fail — they used to land under key 'client' as ok=True."""
    scope = scope_factory(client_a, "operator", domain="operations")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(scope, "memory_add_fact", {"title": "", "content": "", "key": ""})
    assert not outcome.ok
    assert "content" in outcome.message.lower()


def test_memory_add_fact_accepts_aliases_and_sticks(client_a, scope_factory):
    """Models often send memory_key/value instead of key/content."""
    scope = scope_factory(client_a, "operator", domain="operations")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "memory_add_fact",
            {
                "memory_key": "alias_probe_key",
                "value": "Alias probe value sticks",
                "title": "Alias probe",
            },
        )
        assert outcome.ok, outcome.message
        got = guarded_call(scope, "memory_get_fact", {"key": "alias_probe_key"})
    assert got.ok and got.data is not None
    assert got.data["value"] == "Alias probe value sticks"
    assert got.data["key"] == "alias_probe_key"


def test_memory_put_requires_key_or_title(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        with pytest.raises(ValueError, match="key or a non-empty title"):
            memory.put(
                category=MemoryCategory.FACT,
                title="",
                content="x",
                memory_key=None,
                settings=client_a.settings,
                ctx=ctx,
            )


def test_tool_argument_schemas_expose_memory_fields():
    from bizos.agents.tool_args import normalize_tool_arguments, schema_for_tool

    schema = schema_for_tool("memory_add_fact")
    assert "content" in schema["properties"]
    assert "title" in schema["required"]
    assert schema.get("additionalProperties") is False
    assert normalize_tool_arguments({"kwargs": {"key": "a", "content": "b"}}) == {
        "key": "a",
        "content": "b",
    }

