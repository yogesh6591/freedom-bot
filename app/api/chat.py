"""
Chat Routes
===========

§26 ``/api/chat``.

The agent is constructed per request and bound to exactly one tenant (see
:mod:`bizos.agents.agent` for why). The response carries the structured signals
the UI needs — citations, tool activity, whether something is awaiting approval —
without dumping raw traces at ordinary users (§17).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, run_scope
from bizos.actions import store as actions
from bizos.agents.agent import ModelNotConfigured, build_agent
from bizos.agents.instructions import assistant_name
from bizos.agents.persona import AUTO, enforce_persona, route
from bizos.rbac.registry import TOOL_SPECS
from bizos.control.models import ClientSettings
from bizos.policy.modes import describe as describe_mode
from bizos.tenancy.context import TenantContext
from bizos.types import ActionStatus
from bizos.util.ids import new_id

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: Optional[str] = None
    #: "auto" (the default) routes each turn to the domain it is about, inside
    #: one continuous session. A named domain pins the turn to that domain.
    domain: str = AUTO
    #: The domain the previous turn was routed to, so short follow-ups stay put.
    previous_domain: Optional[str] = None
    #: May only narrow the workspace mode.
    execution_mode: Optional[str] = None


@router.get("/context")
def chat_context(
    domain: str = AUTO,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """What the agent can do right now — drives the UI's mode banner and tool list.

    Lists every process the caller may *see* in their enabled domains, each
    flagged with whether they may *run* it (FB-037).
    """
    from bizos.agents.surface import visible_specs
    from bizos.domains.packs import enabled_packs, get_pack

    with bound(ctx):
        scope = run_scope(ctx, settings, domain="general" if domain == AUTO else domain)
        if domain == AUTO:
            packs = enabled_packs(settings.enabled_domains)
        else:
            packs = [p for p in (get_pack(domain), get_pack("general")) if p is not None]
        runnable = {s.name for s in visible_specs(scope, packs)}
        in_packs = {spec.name for p in packs for spec in p.tool_specs()}
        viewable = [
            s for s in TOOL_SPECS
            if s.name in in_packs and s.implemented and s.can_view(ctx)
        ]
        return {
            "mode": str(scope.mode),
            "mode_description": describe_mode(scope.mode),
            "domain": domain,
            "enabled_domains": settings.enabled_domains,
            "assistant_name": assistant_name(settings),
            "role": str(ctx.primary_role),
            "data_scopes": sorted(ctx.visible_areas),
            "tools": [
                {
                    "name": s.name,
                    "title": s.title,
                    "write": s.write,
                    "risk": str(s.risk),
                    "category": s.category,
                    "can_view": True,
                    "can_run": s.name in runnable,
                    "tool_mode": settings.tool_modes.get(s.name),
                }
                for s in viewable
            ],
            "pending_approvals": len(actions.pending_approvals(ctx=ctx)),
        }


@router.post("")
def chat(
    body: ChatRequest,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """One chat turn."""
    session_id = body.session_id or new_id("ses")
    if body.domain == AUTO:
        domain = route(body.message, settings.enabled_domains, previous=body.previous_domain)
    else:
        domain = body.domain
    with bound(ctx):
        scope = run_scope(
            ctx,
            settings,
            domain=domain,
            requested_mode=body.execution_mode,
            session_id=session_id,
        )
        before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}

        try:
            agent = build_agent(scope, session_id=session_id)
        except ModelNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        try:
            output = agent.run(body.message)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"The agent could not complete: {exc}") from exc

        # Anything the turn created that a human now needs to look at.
        new_actions = [
            a.to_dict()
            for a in actions.list_actions(limit=200, ctx=ctx)
            if a.id not in before
        ]

        content, persona_fixed = enforce_persona(
            str(getattr(output, "content", output) or ""), assistant_name(settings)
        )
        if persona_fixed:
            from bizos.audit.events import log
            from bizos.types import AuditEventType

            log(
                AuditEventType.GUARDRAIL_TRIGGERED,
                ctx=ctx,
                session_id=session_id,
                status="PERSONA_ENFORCED",
                execution_mode=str(scope.mode),
            )

        return {
            "session_id": session_id,
            "content": content,
            "mode": str(scope.mode),
            "domain": domain,
            "routed": body.domain == AUTO,
            "tool_activity": _tool_activity(output),
            "actions": new_actions,
            "awaiting_approval": [
                a for a in new_actions if a["status"] == str(ActionStatus.PENDING_APPROVAL)
            ],
            "drafts": [a for a in new_actions if a["status"] == str(ActionStatus.DRAFT)],
        }


def _tool_activity(output: Any) -> list[dict[str, Any]]:
    """Summarize tool calls for the UI without exposing raw arguments.

    Arguments can contain customer content; the activity strip only needs to say
    *what* ran and whether it succeeded. The full record, redacted, is in the
    audit log for those permitted to see it.
    """
    activity: list[dict[str, Any]] = []
    for call in getattr(output, "tools", None) or []:
        name = getattr(call, "tool_name", None) or (call.get("tool_name") if isinstance(call, dict) else None)
        if not name:
            continue
        error = getattr(call, "tool_call_error", None)
        if isinstance(call, dict):
            error = call.get("tool_call_error")
        activity.append({"tool": name, "ok": not error})
    return activity
