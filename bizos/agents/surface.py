"""
Agent Tool Surface
==================

Builds the list of callables handed to the model for one run.

This is the **first** of the three enforcement layers (see ARCHITECTURE.md): a
tool the caller's role or the workspace's mode forbids is never given to the
model at all. It cannot call what it cannot see, so a jailbreak has nothing to
reach for. The policy engine still re-checks every call that does get made, and
read tools still run on a read-only database session — belt, braces and a
structural floor.

The functions are generated from the central registry, so a tool cannot appear on
the surface without a :class:`~bizos.rbac.registry.ToolSpec` and therefore cannot
appear without permissions and a risk classification.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from agno.tools import tool

from bizos.agents.tool_args import normalize_tool_arguments, schema_for_tool
from bizos.domains.packs import DomainPack, get_pack
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.rbac.registry import ToolSpec, get_spec
from bizos.tools.base import RunScope, guarded_call
from bizos.tools.registry_check import load_all_tools
from bizos.types import PolicyEffect


def _visible(scope: RunScope, spec: ToolSpec) -> bool:
    """Whether this tool may appear on the surface at all.

    A tool is hidden when the policy engine would DENY it outright — wrong role,
    disabled domain, unconnected integration, PHI/card restriction, or a mode that
    forbids the whole class of action. Tools that would come back as DRAFT_ONLY or
    REQUIRE_APPROVAL stay visible: the agent is *supposed* to be able to propose
    those, and the queue is where they land.
    """
    probe = PolicyRequest(
        ctx=scope.ctx,
        settings=scope.settings,
        tool=spec.name,
        requested_mode=scope.requested_mode,
        domain=scope.domain,
        connected_integrations=scope.integrations,
    )
    return evaluate(probe).effect != PolicyEffect.DENY


def visible_specs(scope: RunScope, packs: Optional[list[DomainPack]] = None) -> list[ToolSpec]:
    """The tool specs this run may expose, after role, mode and domain filtering."""
    load_all_tools()
    pack = get_pack(scope.domain)
    candidates: dict[str, ToolSpec] = {}
    for source in packs or ([pack] if pack else []):
        for spec in source.tool_specs():
            candidates[spec.name] = spec
    if not candidates:
        from bizos.rbac.registry import TOOL_SPECS

        candidates = {s.name: s for s in TOOL_SPECS}
    return [s for s in candidates.values() if s.implemented and _visible(scope, s)]


def _describe(spec: ToolSpec, scope: RunScope) -> str:
    """Tool description shown to the model, including what will happen to a write."""
    text = spec.description
    if spec.write:
        probe = PolicyRequest(
            ctx=scope.ctx,
            settings=scope.settings,
            tool=spec.name,
            requested_mode=scope.requested_mode,
            domain=scope.domain,
            connected_integrations=scope.integrations,
        )
        effect = evaluate(probe).effect
        if effect == PolicyEffect.DRAFT_ONLY:
            text += " In this workspace's current mode, calling this prepares a draft; it does not execute."
        elif effect == PolicyEffect.REQUIRE_APPROVAL:
            text += " In this workspace's current mode, calling this creates an approval request; it does not execute."
    return text


def build_tools(scope: RunScope, packs: Optional[list[DomainPack]] = None) -> list[Callable]:
    """Agno-compatible callables for this run.

    Every one routes through :func:`bizos.tools.base.guarded_call`, so there is no
    path from the model to a connector that skips the policy engine.
    """
    tools: list[Callable] = []
    for spec in visible_specs(scope, packs):
        tools.append(_make_tool(scope, spec))
    return tools


def _make_tool(scope: RunScope, spec: ToolSpec) -> Callable:
    """Wrap one spec as a callable bound to this run's scope.

    The entrypoint still accepts ``**kwargs`` so one factory covers every tool,
    but we overwrite Agno's inferred schema with :func:`schema_for_tool`. Without
    that, Agno publishes a nested empty ``kwargs`` object and the model cannot
    pass real fields (blank memory writes, empty approval payloads).
    """
    description = f"{_describe(spec, scope)}\n\nArguments: {ARGUMENT_HINTS.get(spec.name, 'see description')}"

    @tool(name=spec.name, description=description)
    def _invoke(**kwargs: Any) -> str:
        outcome = guarded_call(scope, spec.name, normalize_tool_arguments(kwargs))
        if outcome.data is not None and outcome.ok:
            return f"{outcome.agent_text()}\n\nResult: {outcome.data}"
        return outcome.agent_text()

    # Replace Agno's **kwargs schema before the agent processes the tool.
    _invoke.parameters = schema_for_tool(spec.name)
    return _invoke


#: Argument documentation per tool. Kept beside the surface builder rather than in
#: the registry because it is a *presentation* concern for the model, not a
#: permission concern.
ARGUMENT_HINTS: dict[str, str] = {
    "memory_search": "query (str), categories (list of FACT|SOP|DECISION|CORRECTION, optional), limit (int, optional)",
    "memory_get_fact": "key (str), category (str, default FACT)",
    "memory_history": "item_id (str)",
    "memory_add_fact": "title (str), content (str), key (str, optional), tags (list, optional), source_type (str, optional), confidence (float, optional)",
    "memory_add_sop": "title (str), content (str), key (str, optional), tags (list, optional)",
    "memory_record_decision": "title (str), decision (str), decided_on (str), approved_by (str), context (str), reason (str, optional), status (str, optional)",
    "memory_correct": "key (str) or item_id (str), new_content (str), reason (str)",
    "knowledge_search": "query (str), limit (int, optional)",
    "crm_search_contacts": "query (str), lifecycle (str, optional), limit (int, optional)",
    "crm_get_contact": "contact_id (str) — id or email",
    "crm_search_companies": "query (str), limit (int, optional)",
    "crm_search_deals": "stage (str, optional), owner (str, optional), limit (int, optional)",
    "crm_pipeline_summary": "no arguments",
    "crm_create_note": "body (str), contact_id (str, optional), deal_id (str, optional)",
    "crm_create_task": "title (str), body (str, optional), contact_id (str, optional), assignee (str, optional), due_at (ISO datetime, optional)",
    "crm_tag_lead": "contact_id (str), add_tags (list, optional), remove_tags (list, optional), lifecycle (str, optional)",
    "crm_update_contact_field": "contact_id (str), field (str), value (str)",
    "crm_create_contact": "first_name (str), last_name (str, optional), email (str, optional), company (str, optional), title (str, optional), lifecycle (str, optional), tags (list, optional)",
    "crm_update_deal_stage": "deal_id (str), stage (str)",
    "crm_bulk_update_contacts": "field (str), value (str), lifecycle_filter (str, optional)",
    "crm_delete_record": "record_type (contact|deal|company), record_id (str)",
    "email_search_threads": "query (str), limit (int, optional)",
    "email_read_thread": "thread_id (str)",
    "email_create_draft": "recipients (list of str), subject (str), body (str), cc (list, optional)",
    "email_send_message": "recipients (list of str), subject (str), body (str), cc (list, optional)",
    "calendar_list_events": "start (ISO datetime, optional), end (ISO datetime, optional), limit (int, optional)",
    "calendar_check_availability": "start (ISO datetime), end (ISO datetime), slot_minutes (int, optional)",
    "calendar_create_event": "title (str), starts_at (ISO datetime), ends_at (ISO datetime), attendees (list, optional), description (str, optional), location (str, optional)",
    "calendar_reschedule_event": "event_id (str), starts_at (ISO datetime), ends_at (ISO datetime)",
    "calendar_cancel_event": "event_id (str)",
    "request_human_review": "question (str), context (str), choices (list, optional), recommended_option (str, optional), confidence (float, optional)",
}
