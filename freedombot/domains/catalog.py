"""
Domain Pack Catalogue
=====================

Phase 1 (FB-035) builds General, Operations, Sales, Intake, Planning, Strategy,
Finance, Brand and Legal. Placeholders are gone — each pack has instructions and
tools; finance/legal writes that require a license always go to approval.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainCatalogEntry:
    name: str
    title: str
    description: str
    phase: int
    #: "full" — implemented with tools and instructions.
    #: "placeholder" — configuration and instructions only; no domain tools yet.
    depth: str


DOMAIN_CATALOG: tuple[DomainCatalogEntry, ...] = (
    DomainCatalogEntry(
        "general",
        "General",
        "Organizational memory and everyday questions. Always enabled.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "operations",
        "Operations",
        "Internal tasks, process execution, SOP adherence and operational reporting.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "sales",
        "Sales",
        "Pipeline, deals, contacts, outreach drafting and sales reporting.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "intake",
        "Intake",
        "Inbound lead capture, enrichment, deduplication, classification and routing.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "planning",
        "Planning",
        "Calendars, meeting preparation, scheduling and weekly planning.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "strategy",
        "Strategy",
        "Priorities, options memos and strategic analysis. Advise-first; options need humans.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "finance",
        "Finance",
        "Finance policy lookup and licensed adjustment proposals (always approved).",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "brand",
        "Brand",
        "Brand voice guidelines and draft messaging. Draft ceiling — no publish.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "legal",
        "Legal",
        "Clause lookup and counsel escalation. Never renders legal judgment.",
        1,
        "full",
    ),
)

DOMAIN_NAMES: tuple[str, ...] = tuple(entry.name for entry in DOMAIN_CATALOG)

#: The base package a new client gets at provisioning time (FB-035). The other
#: domains (strategy, finance, brand, legal) are add-ons: they are fully built,
#: but a client gets them only by a recorded change order.
DEFAULT_ENABLED_DOMAINS: tuple[str, ...] = (
    "general",
    "operations",
    "sales",
    "intake",
    "planning",
)


def get_entry(name: str) -> DomainCatalogEntry | None:
    """Look up a catalogue entry by name."""
    folded = (name or "").strip().casefold()
    return next((e for e in DOMAIN_CATALOG if e.name == folded), None)
