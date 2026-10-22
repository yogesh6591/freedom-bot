"""
Routing and Tool Loading (v2 pipeline)
======================================

Plain Python: which tools this user may see, which routing domains the router may choose from, and
which tools a route loads (docs/agent/ARCHITECTURE.md §5.2–5.4, §7).

Visibility is the existing policy check with no arguments and no preconditions, run in the pack scope
the live assistant would use (:func:`bizos.tools.dry_run.is_visible`). A tool it denies is hidden from
the model and from ``load_tools``, whatever the route says. Routing domains only choose among visible
tools; they never make a tool visible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from bizos.agents.merged_tools import MERGED, WRAPPED, schema_for
from bizos.agents.tool_meta import DOMAINS, TOOL_META
from bizos.domains.packs import DomainPack
from bizos.rbac.registry import TOOL_SPECS
from bizos.tools.base import RunScope
from bizos.tools.dry_run import is_visible

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"

LOAD_TOOLS = "load_tools"
#: Loaded on every request, whatever the route.
ALWAYS_ON = ("memory_search", LOAD_TOOLS)

Domain = Literal[
    "memory", "approvals", "crm", "email", "calendar", "finance", "ops", "youtube",
    "admin_setup", "admin_launch", "admin_alerts", "admin_users", "admin_permissions",
    "admin_commercial", "campaign",
]

#: What the router reads for each domain (ARCHITECTURE.md §7).
DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "memory": "Company knowledge: search facts and SOPs, look up a fact or its history, add facts, SOPs and decisions, correct facts, escalate conflicts (policy, contract and brand questions also need finance or ops)",
    "approvals": "Approval requests: list, view, submit, approve/reject/request changes, cancel",
    "crm": "Contacts, companies, deals: search, view, notes, tasks, tags, create contacts, update contact fields, bulk updates by lifecycle stage, deal stages, delete records",
    "email": "Email: search and read threads, draft, send to named people",
    "calendar": "Meetings: list, check availability, create, reschedule, cancel",
    "finance": "Finance policies (discounts, refunds, credits, payment terms), pending billables, invoices, invoice drafts, adjustments",
    "ops": "Legal clauses and contract terms, brand voice and tone, strategy priorities, candidate matching, counsel flags, support tickets, n8n automations",
    "youtube": "YouTube: search videos, our channel and videos, one video's details and comments",
    "admin_setup": "Workspace config: overview, setup, settings, enable or disable domain packs",
    "admin_launch": "Go-live: launch checklist, mark items, sign-off, handoff readiness",
    "admin_alerts": "Workspace alerts: list, rescan, dismiss, alert baselines",
    "admin_users": "BizOS login accounts: list, create, roles and data access, suspend or reactivate, password reset",
    "admin_permissions": "Tool rules: which roles can use a tool, and tightening how it runs",
    "admin_commercial": "Pricing, resale rules, consulting quotes, change orders",
    "campaign": "Outbound email campaigns (admins only): pick prospects, pick or create a campaign and send window, propose the email, approve moving people between campaigns, send after approval",
}

#: Tools from other domains a bundle also loads (ARCHITECTURE.md §7).
BUNDLE_EXTRAS: dict[str, tuple[str, ...]] = {
    "email": ("crm_search_contacts", "crm_get_contact", "brand_get_voice"),
    "calendar": ("crm_search_contacts", "crm_get_contact"),
    "finance": ("crm_search_companies",),
}

#: Every tool the v2 model can ever be given: merged tools replace the old tools they wrap.
MODEL_TOOLS: frozenset[str] = frozenset((({s.name for s in TOOL_SPECS} - WRAPPED) | set(MERGED)))


def home_tools(domain: str) -> tuple[str, ...]:
    """The tools whose routing domain is ``domain``."""
    return tuple(sorted(n for n in MODEL_TOOLS if TOOL_META[n].domain == domain))


def bundle(domain: str) -> tuple[str, ...]:
    """What routing to ``domain`` loads, before the visibility filter."""
    return home_tools(domain) + tuple(t for t in BUNDLE_EXTRAS.get(domain, ()) if t not in home_tools(domain))


class Route(BaseModel):
    domains: list[Domain] = Field(default_factory=list, max_length=4)
    kind: Literal["task", "question", "chitchat", "out_of_scope"]
    entities: dict[str, str] = Field(default_factory=dict)
    clarify: Optional[str] = None


# --------------------------------------------------------------------------- visibility


def visible_tools(scope: RunScope, packs: list[DomainPack], *, campaign_allowed: bool) -> frozenset[str]:
    """Every v2 tool this user may be shown in this request.

    A registered tool is visible when the policy check (no arguments, no preconditions) does not deny it.
    A merged tool is visible when at least one old tool it maps to is. Campaign tools also need
    ``campaign_allowed`` (an admin in the general or acquisition chat), as in the current assistant.
    """
    wrapped_ok = set(allowed_old_tools(scope, packs))
    out: set[str] = set()
    for name in MODEL_TOOLS:
        if name in MERGED:
            if schema_for(name, wrapped_ok) is not None:
                out.add(name)
        elif TOOL_META[name].domain == "campaign":
            if campaign_allowed and is_visible(scope, name, packs):
                out.add(name)
        elif is_visible(scope, name, packs):
            out.add(name)
    return frozenset(out)


def allowed_old_tools(scope: RunScope, packs: list[DomainPack]) -> frozenset[str]:
    """Old tools behind merged tools that this user may use (for narrowing merged schemas)."""
    return frozenset(t for m in MERGED.values() for t in m.wraps if is_visible(scope, t, packs))


def domain_menu(visible: frozenset[str]) -> list[str]:
    """Routing domains the router may choose: those with at least one visible tool of their own."""
    return [d for d in DOMAINS if any(t in visible for t in home_tools(d))]


# --------------------------------------------------------------------------- loading


#: Admin requests often need a tool from a neighbouring admin domain ("make every email a draft" is routed to
#: admin_setup but needs admin_set_tool_mode), so routing to any of these loads all of them that are visible.
ADMIN_FAMILY = ("admin_setup", "admin_permissions", "admin_users", "admin_launch", "admin_alerts", "admin_commercial")


def tools_for_route(domains: list[str], visible: frozenset[str]) -> list[str]:
    """(bundles of the routed domains ∪ ALWAYS_ON) ∩ visible. ``load_tools`` is always included.

    Any admin domain brings the whole admin family (ADMIN_FAMILY)."""
    if any(d in ADMIN_FAMILY for d in domains):
        domains = list(domains) + [d for d in ADMIN_FAMILY if d not in domains]
    wanted: list[str] = []
    for domain in domains:
        for tool in bundle(domain):
            if tool not in wanted:
                wanted.append(tool)
    loaded = [t for t in ALWAYS_ON if t == LOAD_TOOLS or t in visible]
    loaded += [t for t in wanted if t in visible and t not in loaded]
    return loaded


CAMPAIGN = "campaign"
#: What the executor is told when it asks for the campaign tools mid-request.
CAMPAIGN_ELSEWHERE = (
    "Campaign requests are handled on their own, not in this request, so no campaign tools were loaded. "
    "Finish the other parts of the request. Then tell the user that the campaign part was not done, and "
    "that they should send it as a separate message (for example \"send the approved campaign emails\")."
)


@dataclass
class ToolLoader:
    """The tools loaded for one request, and the executor's ``load_tools`` calls (each a router miss)."""

    visible: frozenset[str]
    loaded: list[str]
    misses: list[dict] = field(default_factory=list)

    @classmethod
    def for_route(cls, route: Route, visible: frozenset[str]) -> "ToolLoader":
        menu = domain_menu(visible)
        return cls(visible, tools_for_route([d for d in route.domains if d in menu], visible))

    def load_tools(self, domain: str) -> tuple[list[str], str]:
        """Add a domain the router missed. Returns (newly loaded tools, message for the model)."""
        domain = str(domain or "").strip().casefold()
        handed_off = domain == CAMPAIGN and any(t in self.visible for t in home_tools(domain))
        added = (
            [t for t in bundle(domain) if t in self.visible and t not in self.loaded]
            if domain in DOMAINS and not handed_off else []
        )
        self.loaded.extend(added)
        self.misses.append({"domain": domain, "added": added})
        log.info("v2 load_tools router miss: domain=%s added=%s", domain, added)
        if domain not in DOMAINS:
            return [], f"There is no {domain!r} domain. Domains: {', '.join(domain_menu(self.visible))}."
        if not any(t in self.visible for t in home_tools(domain)):
            return [], f"The {domain} tools are not available to this user, so say so plainly."
        if handed_off:
            # The campaign flow runs only in a turn of its own (it is handed to the current assistant).
            return [], CAMPAIGN_ELSEWHERE
        if not added:
            return [], f"The {domain} tools are already loaded."
        return added, f"Loaded {', '.join(added)}."


# --------------------------------------------------------------------------- router prompt


def render_router_prompt(
    *, user_name: str, user_roles: list[str], today: str, timezone: str, recent_messages: str, menu: list[str]
) -> str:
    """bizos/agents/prompts/router.md with its placeholders filled. The JSON examples keep their braces."""
    available = "\n".join(f"- {d}: {DOMAIN_DESCRIPTIONS[d]}" for d in menu) or "- (none)"
    text = (PROMPTS / "router.md").read_text(encoding="utf-8")
    for key, value in {
        "{user_name}": user_name,
        "{user_roles}": ", ".join(user_roles),
        "{today}": today,
        "{timezone}": timezone,
        "{recent_messages}": recent_messages or "(none)",
        "{available_domains}": available,
    }.items():
        text = text.replace(key, value)
    return text


def clean_route(route: Route, menu: list[str]) -> Route:
    """Drop any domain the user was not offered. Code enforces what the prompt asks."""
    kept = [d for d in route.domains if d in menu]
    if len(kept) != len(route.domains):
        log.info("v2 router chose unavailable domains: %s", [d for d in route.domains if d not in menu])
    return route.model_copy(update={"domains": kept})


# --------------------------------------------------------------------------- playbooks

_TOOL_WORD = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
#: Every tool name a playbook line could mention (old, merged and campaign tools).
KNOWN_TOOLS: frozenset[str] = frozenset({s.name for s in TOOL_SPECS} | set(MERGED) | {LOAD_TOOLS})


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def _items(lines: list[str]) -> list[list[str]]:
    """Group a section's lines into bullets: a "- " line plus its indented continuation lines."""
    items: list[list[str]] = []
    for line in lines:
        if line.startswith("- ") or not items:
            items.append([line])
        else:
            items[-1].append(line)
    return items


def render_playbooks(domains: list[str], visible: frozenset[str], *, timezone: str, default_meeting_minutes: int = 30) -> str:
    """The playbook sections for the routed domains, without any line that names a tool this user cannot see.

    Covers pack-gated tools such as brand_get_voice: a playbook never tells the model to call a tool it
    was not given.
    """
    sections = _sections((PROMPTS / "playbooks.md").read_text(encoding="utf-8"))
    rendered: list[str] = []
    for domain in domains:
        kept = []
        for item in _items(sections.get(domain, [])):
            named = {w for w in _TOOL_WORD.findall(" ".join(item)) if w in KNOWN_TOOLS}
            if named - visible - {LOAD_TOOLS}:
                continue
            kept.extend(line for line in item if line.strip())
        if kept:
            rendered.append(f"## {domain}\n" + "\n".join(kept))
    text = "\n\n".join(rendered)
    return text.replace("{timezone}", timezone).replace("{default_meeting_minutes}", str(default_meeting_minutes))
