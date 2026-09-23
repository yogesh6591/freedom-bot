"""
Chat Routes
===========

§26 ``/api/chat``.

The agent is constructed per request and bound to exactly one tenant (see
:mod:`bizos.agents.agent` for why). The response carries the structured signals
the UI needs — citations, tool activity, whether something is awaiting approval,
workflow status — without dumping raw traces at ordinary users (§17).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, run_scope
from bizos.actions import store as actions
from bizos.agents.agent import ModelNotConfigured, build_agent
from bizos.control.models import ClientSettings
from bizos.policy.modes import describe as describe_mode
from bizos.review import store as reviews
from bizos.tenancy.context import TenantContext
from bizos.types import ActionStatus
from bizos.util.ids import new_id

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: Optional[str] = None
    domain: str = "general"
    #: May only narrow the workspace mode.
    execution_mode: Optional[str] = None


@router.get("/context")
def chat_context(
    domain: str = "general",
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """What the agent can do right now — drives the UI's mode banner and tool list."""
    from bizos.agents.surface import visible_specs
    from bizos.domains.packs import get_pack

    with bound(ctx):
        scope = run_scope(ctx, settings, domain=domain)
        pack = get_pack(domain)
        general = get_pack("general")
        packs = [p for p in (pack, general) if p is not None]
        specs = visible_specs(scope, packs)
        return {
            "mode": str(scope.mode),
            "mode_description": describe_mode(scope.mode),
            "domain": domain,
            "role": str(ctx.primary_role),
            "tools": [
                {
                    "name": s.name,
                    "title": s.title,
                    "write": s.write,
                    "risk": str(s.risk),
                    "category": s.category,
                }
                for s in specs
            ],
            "pending_approvals": len(actions.pending_approvals(ctx=ctx)),
            "open_reviews": reviews.open_count(ctx),
        }


@router.post("")
def chat(
    body: ChatRequest,
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """One chat turn."""
    session_id = body.session_id or new_id("ses")
    with bound(ctx):
        scope = run_scope(
            ctx,
            settings,
            domain=body.domain,
            requested_mode=body.execution_mode,
            session_id=session_id,
        )
        before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}
        open_reviews_before = {r.id for r in reviews.list_items(limit=200, ctx=ctx)}

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
        new_reviews = [
            r.to_dict() for r in reviews.list_items(limit=200, ctx=ctx) if r.id not in open_reviews_before
        ]

        return {
            "session_id": session_id,
            "content": getattr(output, "content", str(output)),
            "mode": str(scope.mode),
            "domain": body.domain,
            "tool_activity": _tool_activity(output),
            "actions": new_actions,
            "awaiting_approval": [
                a for a in new_actions if a["status"] == str(ActionStatus.PENDING_APPROVAL)
            ],
            "drafts": [a for a in new_actions if a["status"] == str(ActionStatus.DRAFT)],
            "reviews": new_reviews,
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
