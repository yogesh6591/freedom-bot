"""M04-12 open conflicts must not be answered silently; M04-17 synonym titles conflict."""

from __future__ import annotations

from bizos.agents import grounding
from bizos.memory import store as memory
from bizos.tenancy.context import tenant_scope
from bizos.types import ApprovalStatus, MemoryCategory


def test_topic_fingerprint_collapses_hq_synonyms():
    assert memory.topic_fingerprint("HQ city") == memory.topic_fingerprint("Headquarters city")
    assert memory.topic_fingerprint("HQ city") == "city hq"


def test_synonym_titles_open_a_conflict_instead_of_two_approved(client_a, ctx_factory):
    admin = ctx_factory(client_a, "admin")
    with tenant_scope(admin):
        first = memory.put(
            category=MemoryCategory.FACT,
            memory_key="hq_city_boston",
            title="HQ city",
            content="Boston",
            settings=client_a.settings,
            ctx=admin,
        )
        second = memory.put(
            category=MemoryCategory.FACT,
            memory_key="headquarters_city_chicago",
            title="Headquarters city",
            content="Chicago",
            settings=client_a.settings,
            ctx=admin,
        )
    assert first.current.approval_status == ApprovalStatus.APPROVED
    assert second.current.approval_status == ApprovalStatus.PENDING
    assert first.id in (second.current.attributes or {}).get("conflict_with", [])
    with tenant_scope(admin):
        topics = {c["topic"] for c in memory.find_conflicts(ctx=admin)}
    assert memory.topic_fingerprint("HQ city") in topics


def test_grounding_hides_conflicted_facts_and_names_the_conflict(client_a, ctx_factory):
    admin = ctx_factory(client_a, "admin")
    with tenant_scope(admin):
        memory.put(
            category=MemoryCategory.FACT,
            memory_key="receipt_threshold_low",
            title="Receipt threshold A",
            content="Receipts required over $25",
            attributes={"topic": "receipt_threshold_m04"},
            settings=client_a.settings,
            ctx=admin,
        )
        memory.put(
            category=MemoryCategory.FACT,
            memory_key="receipt_threshold_high",
            title="Receipt threshold B",
            content="Receipts required over $75",
            attributes={"topic": "receipt_threshold_m04"},
            settings=client_a.settings,
            ctx=admin,
        )
        grounded = grounding.analyze(
            admin, "What is the receipt threshold policy for expenses?", list(client_a.settings.enabled_domains)
        )
    assert grounded.conflicts
    assert "OPEN POLICY CONFLICT" in grounded.text
    assert "$25" in grounded.text and "$75" in grounded.text
    # Approved side must not be offered as a plain Fact: line to quote.
    assert "Fact: Receipts required over $25" not in grounded.text


def test_enforce_conflicts_rewrites_silent_choice():
    conflicts = [
        {
            "topic": "receipt_threshold_m04",
            "items": [
                {"value": "Receipts required over $25", "title": "A"},
                {"value": "Receipts required over $75", "title": "B"},
            ],
        }
    ]
    fixed, changed = grounding.enforce_conflicts(
        "Fact: The receipt threshold policy for expenses requires receipts for amounts over $25.",
        conflicts,
    )
    assert changed
    assert "disagree" in fixed.casefold()
    assert "$25" in fixed and "$75" in fixed
    untouched, changed = grounding.enforce_conflicts(
        "There is an open conflict on this policy; an approver must decide.",
        conflicts,
    )
    assert not changed and untouched.startswith("There is an open conflict")
