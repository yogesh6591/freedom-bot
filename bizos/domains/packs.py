"""
Domain Packs
============

§11: a modular pack architecture where each domain may declare its own
instructions, tools, knowledge scope, workflows, guardrails, permissions and
approval requirements — and an admin can enable or disable each pack per client.

A pack is **declarative**. It selects from the central tool registry rather than
defining tools of its own, so there is still exactly one place where a tool's
permissions are decided (§7). A pack can only *narrow*: it may restrict the
execution mode or add an approval requirement, never widen either.

Phase 1 (§12) implements General, Operations, Sales, Intake and Planning to depth.
Strategy, Finance, Brand and Legal are registered as configuration-only
placeholders — deliberately shallow, as the brief asks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bizos.domains.catalog import DOMAIN_CATALOG, DomainCatalogEntry, get_entry
from bizos.rbac.registry import ToolSpec, specs_for_domain
from bizos.types import ExecutionMode


@dataclass(frozen=True)
class DomainPack:
    """One business capability domain."""

    name: str
    title: str
    #: Prepended to the agent's system prompt when this domain is active.
    instructions: str
    #: Tool names this pack exposes. Empty means "every tool that lists this
    #: domain in the central registry".
    tools: tuple[str, ...] = ()
    #: Tags used to scope knowledge search within the domain.
    knowledge_tags: tuple[str, ...] = ()
    #: Workflow template ids this domain owns.
    workflows: tuple[str, ...] = ()
    #: Domain-level mode ceiling. May only narrow the workspace default.
    mode_ceiling: Optional[ExecutionMode] = None
    #: Tools this domain always sends to approval, whatever the mode.
    always_approve: tuple[str, ...] = ()
    #: Extra guardrail text, rendered into the prompt.
    guardrails: tuple[str, ...] = ()
    #: Output shape the domain prefers.
    output_template: str = ""

    @property
    def entry(self) -> Optional[DomainCatalogEntry]:
        return get_entry(self.name)

    @property
    def is_placeholder(self) -> bool:
        entry = self.entry
        return entry is not None and entry.depth == "placeholder"

    def tool_specs(self) -> tuple[ToolSpec, ...]:
        """The specs this pack exposes, resolved from the central registry."""
        if self.tools:
            from bizos.rbac.registry import get_spec

            resolved = [get_spec(name) for name in self.tools]
            return tuple(s for s in resolved if s is not None)
        return specs_for_domain(self.name)

    def to_dict(self) -> dict:
        entry = self.entry
        return {
            "name": self.name,
            "title": self.title,
            "description": entry.description if entry else "",
            "phase": entry.phase if entry else 1,
            "depth": entry.depth if entry else "full",
            "tools": [s.name for s in self.tool_specs()],
            "workflows": list(self.workflows),
            "mode_ceiling": str(self.mode_ceiling) if self.mode_ceiling else None,
            "always_approve": list(self.always_approve),
            "guardrails": list(self.guardrails),
        }


GENERAL = DomainPack(
    name="general",
    title="General",
    instructions=(
        "Answer questions using organizational memory and the knowledge base first. When you "
        "state a fact the organization has recorded, cite where it came from and when it was "
        "last updated. If the user tells you something has changed, record it as a correction "
        "so the previous value is superseded rather than silently overwritten."
    ),
    tools=(
        "memory_search",
        "memory_get_fact",
        "memory_history",
        "memory_add_fact",
        "memory_add_sop",
        "memory_record_decision",
        "memory_correct",
        "knowledge_search",
        "request_human_review",
    ),
    knowledge_tags=("general",),
)

OPERATIONS = DomainPack(
    name="operations",
    title="Operations",
    instructions=(
        "You support day-to-day operations. Follow the organization's recorded SOPs exactly; if "
        "an SOP does not cover the situation, say so rather than improvising. Prefer creating "
        "internal tasks and notes over changing customer-facing records."
    ),
    knowledge_tags=("sop", "process", "operations"),
    workflows=("weekly_sales_summary",),
    guardrails=(
        "Never invent an SOP. If no recorded procedure covers the request, raise it for human "
        "review instead of guessing.",
    ),
    output_template="State the SOP you are following, then the steps you took or propose.",
)

SALES = DomainPack(
    name="sales",
    title="Sales",
    instructions=(
        "You support the sales team: pipeline questions, deal context, contact history and "
        "outreach drafting. Ground every claim about a deal in the CRM record you read. Never "
        "promise pricing or terms that are not recorded in organizational memory."
    ),
    knowledge_tags=("sales", "pricing", "product"),
    workflows=("weekly_sales_summary",),
    guardrails=(
        "Do not state pricing, discounts or contract terms that are not in organizational "
        "memory or the CRM. If you cannot find them, say so.",
    ),
    output_template="Lead with the number or the answer, then the supporting records.",
)

INTAKE = DomainPack(
    name="intake",
    title="Intake",
    instructions=(
        "You handle inbound leads and requests. Enrich only from permitted sources, always "
        "check for an existing record before creating a new one, and classify using the "
        "organization's recorded criteria. When you cannot confidently classify or route "
        "something, raise it for human review instead of choosing arbitrarily."
    ),
    knowledge_tags=("intake", "qualification"),
    workflows=("lead_intake",),
    guardrails=(
        "Never create a duplicate contact. If a record with the same email exists, update it.",
        "If the correct owner, project or routing is ambiguous, raise a human review item.",
    ),
)

PLANNING = DomainPack(
    name="planning",
    title="Planning",
    instructions=(
        "You handle calendars, meeting preparation and weekly planning. Check availability "
        "before proposing a time. A meeting brief should cover who the attendees are, the "
        "recent history with them, and what is still open."
    ),
    knowledge_tags=("planning", "meetings"),
    workflows=("meeting_preparation",),
)

# --- Phase 2 placeholders: configuration and instructions only (§12) --------

STRATEGY = DomainPack(
    name="strategy",
    title="Strategy",
    instructions=(
        "You assist with strategic analysis. In this deployment the Strategy pack is advisory "
        "only: research, summarize and lay out options, but do not act on them."
    ),
    tools=("memory_search", "knowledge_search", "memory_history", "request_human_review"),
    mode_ceiling=ExecutionMode.ADVISE,
)

FINANCE = DomainPack(
    name="finance",
    title="Finance",
    instructions=(
        "You assist with financial questions using the workspace accounting tools. "
        "You may search invoices and read customer balances. Recording a payment always "
        "requires human approval — never claim a payment has cleared until an approver "
        "has released it. Do not present output as regulated financial advice."
    ),
    tools=(
        "memory_search",
        "knowledge_search",
        "invoice_search",
        "accounting_customer_balance",
        "accounting_record_payment",
        "request_human_review",
    ),
    mode_ceiling=ExecutionMode.WAIT_FOR_APPROVAL,
    always_approve=("accounting_record_payment",),
    guardrails=(
        "Never present output as regulated financial advice. Any figure you give is a summary "
        "of recorded data, to be confirmed by a qualified person.",
    ),
)

BRAND = DomainPack(
    name="brand",
    title="Brand",
    instructions=(
        "You assist with brand voice and messaging review. In this deployment the Brand pack "
        "prepares drafts only; publishing is out of scope."
    ),
    tools=("memory_search", "knowledge_search", "email_create_draft", "request_human_review"),
    mode_ceiling=ExecutionMode.DRAFT,
)

LEGAL = DomainPack(
    name="legal",
    title="Legal",
    instructions=(
        "You assist with contract lookup and summarization. You do NOT give legal advice and "
        "you do NOT make legal determinations. Summarize what a document says, identify the "
        "clauses that bear on a question, and hand the judgment to a qualified human."
    ),
    tools=("memory_search", "knowledge_search", "memory_history", "request_human_review"),
    mode_ceiling=ExecutionMode.ADVISE,
    guardrails=(
        "Never state a legal conclusion, a risk determination or an interpretation as "
        "authoritative. Every legal judgment requires a licensed human.",
    ),
)

PACKS: tuple[DomainPack, ...] = (
    GENERAL,
    OPERATIONS,
    SALES,
    INTAKE,
    PLANNING,
    STRATEGY,
    FINANCE,
    BRAND,
    LEGAL,
)

PACKS_BY_NAME = {p.name: p for p in PACKS}


def get_pack(name: str) -> Optional[DomainPack]:
    return PACKS_BY_NAME.get((name or "").strip().casefold())


def enabled_packs(enabled: list[str]) -> list[DomainPack]:
    """The packs a client has switched on, in catalogue order."""
    wanted = {d.strip().casefold() for d in enabled}
    return [PACKS_BY_NAME[e.name] for e in DOMAIN_CATALOG if e.name in wanted and e.name in PACKS_BY_NAME]


def assert_catalog_complete() -> None:
    """Every catalogue entry must have a pack, and vice versa."""
    catalog = {e.name for e in DOMAIN_CATALOG}
    packs = set(PACKS_BY_NAME)
    if catalog != packs:
        raise AssertionError(
            f"Domain catalogue and packs disagree: catalogue-only={sorted(catalog - packs)}, "
            f"packs-only={sorted(packs - catalog)}"
        )
