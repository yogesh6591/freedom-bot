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

import copy
from dataclasses import replace
from typing import Any, Callable, Optional

from agno.tools import tool

from bizos.agents.tool_args import normalize_tool_arguments, schema_for_tool
from bizos.domains.packs import DomainPack, get_pack
from bizos.policy.engine import evaluate
from bizos.policy.modes import narrowest
from bizos.util.redaction import redact
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


# --------------------------------------------------------------------------- one agent, every domain
#
# The single-assistant mode gives the model every enabled domain's tools at once
# and lets it decide which to use (instead of a keyword router deciding for it).
# Nothing about enforcement loosens: each tool is bound to the scope of the pack it
# came from, so the policy engine still checks that tool's own domain, the pack's
# mode ceiling still narrows only that pack's tools, and the same visibility rules
# decide whether the model sees the tool at all.


def pack_scope(scope: RunScope, pack: DomainPack) -> RunScope:
    """The run scope for tools that belong to ``pack``."""
    mode = scope.requested_mode
    if pack.mode_ceiling is not None:
        mode = narrowest(mode, pack.mode_ceiling)
    return replace(scope, domain=pack.name, requested_mode=mode)


def visible_specs_by_pack(
    scope: RunScope, packs: list[DomainPack]
) -> list[tuple[ToolSpec, RunScope, DomainPack]]:
    """Every tool this run may expose across ``packs``, each with its own scope.

    A tool listed by several packs appears once, under the first pack that lets
    it through.
    """
    load_all_tools()
    seen: dict[str, tuple[ToolSpec, RunScope, DomainPack]] = {}
    for pack in packs:
        bound_scope = pack_scope(scope, pack)
        for spec in pack.tool_specs():
            if spec.name in seen or not spec.implemented:
                continue
            if _visible(bound_scope, spec):
                seen[spec.name] = (spec, bound_scope, pack)
    return list(seen.values())


def _with_why(schema: dict[str, Any]) -> dict[str, Any]:
    """The tool's schema plus a required ``why`` the model must fill in for every call."""
    out = copy.deepcopy(schema) if schema else {"type": "object", "properties": {}}
    out.setdefault("type", "object")
    props = out.setdefault("properties", {})
    props["why"] = {
        "type": "string",
        "description": "1-2 specific sentences: what you understood and why this call is right now.",
    }
    required = list(out.get("required") or [])
    if "why" not in required:
        required.append("why")
    out["required"] = required
    return out


def build_unified_tools(
    scope: RunScope,
    packs: list[DomainPack],
    *,
    on_call: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[list[Callable], dict[str, str]]:
    """Agno callables for every domain, plus a ``tool name -> domain`` map.

    ``on_call`` receives one record per call (tool, domain, why, redacted
    arguments, outcome) so the turn's trace can show what the model did and why.
    """
    tools: list[Callable] = []
    domains: dict[str, str] = {}
    for spec, bound_scope, pack in visible_specs_by_pack(scope, packs):
        tools.append(_make_tool(bound_scope, spec, with_why=True, on_call=on_call))
        domains[spec.name] = pack.name
    return tools, domains


def _make_tool(scope: RunScope, spec: ToolSpec, *, with_why: bool = False, on_call: Optional[Callable[[dict[str, Any]], None]] = None) -> Callable:
    """Wrap one spec as a callable bound to this run's scope.

    The entrypoint still accepts ``**kwargs`` so one factory covers every tool,
    but we overwrite Agno's inferred schema with :func:`schema_for_tool`. Without
    that, Agno publishes a nested empty ``kwargs`` object and the model cannot
    pass real fields (blank memory writes, empty approval payloads).
    """
    description = f"{_describe(spec, scope)}\n\nArguments: {_hint(spec.name)}"

    @tool(name=spec.name, description=description)
    def _invoke(**kwargs: Any) -> str:
        why = ""
        if with_why:
            why = str(kwargs.pop("why", "") or "")
        arguments = normalize_tool_arguments(kwargs)
        outcome = guarded_call(scope, spec.name, arguments)
        if outcome.data is not None and outcome.ok:
            text = f"{outcome.agent_text()}\n\nResult: {outcome.data}"
        else:
            text = outcome.agent_text()
        if on_call is not None:
            try:
                on_call(
                    {
                        "tool": spec.name,
                        "domain": scope.domain,
                        "why": why,
                        "args": str(redact(arguments))[:600],
                        "ok": bool(outcome.ok),
                        "effect": str(outcome.effect),
                        "result": outcome.message[:300],
                    }
                )
            except Exception:  # noqa: BLE001 - tracing must never break a tool call
                pass
        return text

    # Replace Agno's **kwargs schema before the agent processes the tool.
    schema = schema_for_tool(spec.name)
    _invoke.parameters = _with_why(schema) if with_why else schema
    if with_why:
        # Keep optional fields optional (Agno's strict mode would force every field to be required)
        # and keep our schema (Agno would otherwise rebuild it from the **kwargs signature).
        _invoke.strict = False
        _invoke.skip_entrypoint_processing = True
    return _invoke


def _hint(name: str) -> str:
    if name in ARGUMENT_HINTS:
        return ARGUMENT_HINTS[name]
    schema = schema_for_tool(name)
    if schema.get("additionalProperties"):
        return "see description"
    return ", ".join(schema.get("properties") or {}) or "no arguments"


#: Argument documentation per tool. Kept beside the surface builder rather than in
#: the registry because it is a *presentation* concern for the model, not a
#: permission concern.
ARGUMENT_HINTS: dict[str, str] = {
    "youtube_transcript": "video (YouTube id or link), language (str, optional)",
    "integration_find_action": "query (str), app (connected app slug, e.g. gmail or slack, optional), limit (int, optional)",
    "integration_read_action": "action (an action_id from integration_find_action), arguments (object)",
    "integration_write_action": "action (an action_id from integration_find_action), arguments (object)",
    "integration_destructive_action": "action (an action_id from integration_find_action), arguments (object)",
    "web_search": "query (str, no confidential data), limit (int, optional)",
    "web_read": "url (public http(s) address)",
    "synced_search": "query (str, optional), sources (list of gmail|calendar|youtube|hubspot, optional), since (ISO date, optional), limit (int, optional)",
    "memory_search": "query (str), categories (list of FACT|SOP|DECISION|CORRECTION, optional), limit (int, optional)",
    "memory_get_fact": "key (str), category (str, default FACT)",
    "memory_history": "item_id (str)",
    "memory_add_fact": "title (str), content (str), key (str, optional), tags (list, optional), source_type (str, optional), confidence (float, optional)",
    "memory_add_sop": "title (str), content (str), key (str, optional), tags (list, optional)",
    "memory_record_decision": "title (str), decision (str), decided_on (str), approved_by (str), context (str), reason (str, optional), status (str, optional)",
    "memory_correct": "key (str) or item_id (str), new_content (str), reason (str)",
    "memory_escalate_conflict": "topic (str), item_ids (list[str]), question (str)",
    "support_create_ticket": "subject (str), body (str, optional), severity (NORMAL|HIGH|CRITICAL, optional)",
    "support_my_tickets": "no arguments",
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
    "youtube_search_videos": "query (str), limit (int, optional), order (relevance|date|viewCount|rating, optional)",
    "youtube_my_channel": "no arguments",
    "youtube_my_videos": "limit (int, optional)",
    "youtube_video_details": "video_ids (list[str])",
    "youtube_video_comments": "video_id (str), limit (int, optional), order (relevance|time, optional)",
    "calendar_check_availability": "start (ISO datetime), end (ISO datetime), slot_minutes (int, optional)",
    "calendar_create_event": "title (str), starts_at (ISO datetime), ends_at (ISO datetime), attendees (list, optional), description (str, optional), location (str, optional)",
    "calendar_reschedule_event": "event_id (str), starts_at (ISO datetime), ends_at (ISO datetime)",
    "calendar_cancel_event": "event_id (str)",
    "strategy_list_priorities": "query (str, optional)",
    "strategy_record_option": "title (str), content (str), pros (str, optional), cons (str, optional), recommended (bool, optional)",
    "finance_lookup_policy": "query (str, optional)",
    "finance_list_pending_billables": "customer (str, optional), limit (int, optional)",
    "finance_draft_invoice": "customer (str), limit (int, optional)",
    "finance_propose_adjustment": "customer (str), amount (number), reason (str), kind (str, optional)",
    "hr_match_candidate": "candidate_name (str, e.g. 'Jordan Lee'), opening (str, e.g. 'Operations Coordinator'), opening_key/resume_key (str, optional), resume_text (str, optional)",
    "finance_list_invoices": "customer (str, optional), limit (int, optional)",
    "approvals_list": "status (pending|drafts|approved|completed|failed|rejected|cancelled|all, optional), mine (bool, optional), limit (int, optional)",
    "approvals_get": "action_id (str) or match (title words)",
    "approvals_approve": "action_id (str) or match (title words), comment (str, optional)",
    "approvals_reject": "action_id (str) or match (title words), comment (str, optional)",
    "approvals_request_changes": "action_id (str) or match (title words), comment (str)",
    "approvals_submit": "action_id (str) or match (title words)",
    "approvals_cancel": "action_id (str) or match (title words), comment (str, optional)",
    "brand_get_voice": "query (str, optional)",
    "legal_find_clause": "query (str)",
    "legal_flag_for_counsel": "question (str), context (str, optional), urgency (str, optional)",
    "n8n_trigger_webhook": "event (str), payload (object), workflow (str, optional)",
}
