"""
Domain Pack Catalogue
=====================

The static list of domains the platform knows about. Kept separate from the
pack *implementations* (``bizos/domains/packs.py``) so the control plane can seed
and list domains without importing the tool layer.

Phase 1 (§12) builds Operations, Sales, Intake and Planning to depth, plus a
General pack that is always on. Strategy, Finance, Brand and Legal are
registered and admin-toggleable but intentionally shallow — ``depth="placeholder"``
is the machine-readable statement of that, and the UI shows it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainCatalogEntry:
    name: str
    title: str
    description: str
    phase: int
    #: "full" — implemented with tools, instructions and workflows.
    #: "placeholder" — configuration and instructions only; no domain tools yet.
    depth: str


DOMAIN_CATALOG: tuple[DomainCatalogEntry, ...] = (
    DomainCatalogEntry(
        "general",
        "General",
        "Organizational memory, knowledge lookup and everyday questions. Always enabled.",
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
        "Market and competitive analysis, strategic options. Advisory only in Phase 1.",
        2,
        "placeholder",
    ),
    DomainCatalogEntry(
        "finance",
        "Finance",
        "Invoices, balances, payments and financial reporting via workspace accounting.",
        1,
        "full",
    ),
    DomainCatalogEntry(
        "brand",
        "Brand",
        "Voice, messaging and content review. Placeholder in Phase 1.",
        2,
        "placeholder",
    ),
    DomainCatalogEntry(
        "legal",
        "Legal",
        "Contract summarization and clause lookup. Never renders legal judgment; "
        "advisory and draft only, always human-approved.",
        2,
        "placeholder",
    ),
)

DOMAIN_NAMES: tuple[str, ...] = tuple(entry.name for entry in DOMAIN_CATALOG)

#: Domains a new client gets switched on at provisioning time (§12 Phase 1).
DEFAULT_ENABLED_DOMAINS: tuple[str, ...] = (
    "general",
    "operations",
    "sales",
    "intake",
    "planning",
    "finance",
)


def get_entry(name: str) -> DomainCatalogEntry | None:
    """Look up a catalogue entry by name."""
    folded = (name or "").strip().casefold()
    return next((e for e in DOMAIN_CATALOG if e.name == folded), None)
