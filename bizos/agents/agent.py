"""
Agent Construction
==================

Builds an agno ``Agent`` bound to exactly one tenant, one user and one run.

The agent is constructed **per request** rather than registered once at startup.
That is a deliberate multi-tenancy decision: a long-lived agent object shared
across clients would hold a tool list, a database handle and a system prompt
belonging to whichever tenant configured it last. Constructing per request makes
cross-tenant bleed structurally impossible, and agno agents are cheap to build.

Everything expensive is still shared: the model client, the connection pools and
the per-workspace ``PostgresDb`` are all cached.
"""

from __future__ import annotations

from typing import Any, Optional

from agno.agent import Agent
from agno.guardrails import PromptInjectionGuardrail

from bizos import settings as app_settings
from bizos.agents import instructions as agent_instructions
from bizos.agents.surface import build_tools
from bizos.control.models import ClientSettings
from bizos.domains.packs import enabled_packs, get_pack
from bizos.tenancy.agnodb import workspace_db
from bizos.tenancy.context import TenantContext
from bizos.tools.base import RunScope
from bizos.types import ExecutionMode


class ModelNotConfigured(RuntimeError):
    """Chat needs an LLM; everything else in the platform does not."""


def build_scope(
    ctx: TenantContext,
    client_settings: ClientSettings,
    *,
    domain: str = "general",
    requested_mode: Optional[ExecutionMode] = None,
    session_id: Optional[str] = None,
    agent_id: str = "bizos-assistant",
) -> RunScope:
    """The RunScope for one chat turn."""
    return RunScope(
        ctx=ctx,
        settings=client_settings,
        domain=domain,
        requested_mode=requested_mode,
        agent_id=agent_id,
        session_id=session_id,
    )


def build_agent(scope: RunScope, *, session_id: Optional[str] = None) -> Agent:
    """Construct the tenant-bound agent for one run."""
    if not app_settings.llm_available():
        raise ModelNotConfigured(
            "Chat requires a model. Set OPENAI_API_KEY (and MODEL_ID) to enable it. "
            "Memory, approvals, workflows, audit and the connectors work without one."
        )

    packs = enabled_packs(scope.settings.enabled_domains)
    active = get_pack(scope.domain)
    # The active domain's pack leads; General is always included so memory and
    # knowledge are reachable from every domain.
    selected = [p for p in ({active.name: active} if active else {}).values()]
    general = get_pack("general")
    if general is not None and general not in selected:
        selected.append(general)
    if not selected:
        selected = packs

    # A domain pack may narrow the mode but never widen it.
    ceilings = [p.mode_ceiling for p in selected if p.mode_ceiling is not None]
    if ceilings:
        from bizos.policy.modes import narrowest

        scope.requested_mode = narrowest(scope.requested_mode, *ceilings)

    prompt = agent_instructions.render(
        ctx=scope.ctx,
        settings=scope.settings,
        mode=scope.mode,
        domain=scope.domain,
        packs=selected,
    )

    return Agent(
        name="Business Assistant",
        id="bizos-assistant",
        model=app_settings.model_id(),
        user_id=scope.ctx.user_id,
        session_id=session_id,
        db=workspace_db(scope.ctx),
        instructions=prompt,
        tools=build_tools(scope, selected),
        # The tool list is already resolved for this run; do not let agno cache
        # it across runs, which would be a cross-tenant hazard.
        cache_callables=False,
        # Agno's input guardrail. Defence in depth only: the real protection is
        # that the tool surface and the policy engine never consult message text.
        pre_hooks=[PromptInjectionGuardrail()],
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        # Conversational per-user memory, isolated to this workspace's agno tables.
        # Organizational memory is separate and explicit (bizos.memory).
        enable_agentic_memory=False,
        add_memories_to_context=True,
        telemetry=False,
        store_events=False,
    )


def chat(
    scope: RunScope, message: str, *, session_id: Optional[str] = None, stream: bool = False
) -> Any:
    """One chat turn. Returns agno's run output."""
    agent = build_agent(scope, session_id=session_id)
    return agent.run(message, stream=stream)
