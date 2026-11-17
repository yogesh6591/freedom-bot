"""Small helpers the chat pipeline (``app.assistant.v2``) shares.

The old campaign assistant that lived here is gone; outreach now runs through
the routed pipeline and ``app.outreach``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from agno.models.message import Message

from freedombot import settings as app_settings
from freedombot.agents.persona import AUTO
from freedombot.domains.packs import DomainPack, enabled_packs, get_pack

log = logging.getLogger(__name__)

_effort = {"tried": []}  # effort settings already rejected by the provider, so we stop retrying them
_api = {"responses": False}  # set when the provider says this model needs /v1/responses (never lowers reasoning)


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")



def _effort_candidates() -> list[Optional[str]]:
    """Reasoning-effort settings to try, in order. ``None`` means do not send the parameter.

    By default ONLY the configured setting is used: medium means medium, and a refusal is shown as it is.
    Set MODEL_REASONING_FALLBACK=on to let the app retry with "none" and then without the parameter.
    """
    configured = app_settings.assistant_reasoning_effort()
    chain: list[Optional[str]] = [configured]
    if _truthy("MODEL_REASONING_FALLBACK"):
        for fallback in ("none", None):
            if fallback not in chain:
                chain.append(fallback)
    return [e for e in chain if e not in _effort["tried"]] or [configured]



def _responses_model(**kwargs: Any):
    """A /v1/responses model that keeps each tool call together with its reasoning item.

    Agno only chains steps through ``previous_response_id`` for model names it knows (o3, o4-mini, gpt-5).
    For any other model that reasons (gpt-6-luna), it would resend the tool call WITHOUT its reasoning item
    and the provider refuses ("function_call was provided without its required reasoning item"). Treating
    the model as a reasoning model makes Agno chain the steps the supported way.
    """
    from agno.models.openai import OpenAIResponses

    class _ReasoningResponses(OpenAIResponses):
        def _using_reasoning_model(self) -> bool:
            return bool(self.reasoning_effort) or super()._using_reasoning_model()

    return _ReasoningResponses(**kwargs)


def _openai_model(mid: str, effort: Optional[str]):
    """OpenAI Chat or Responses. Never sets temperature/top_p (reasoning models reject them)."""
    provider, _, name = mid.partition(":")
    if provider == "openai" and name:
        kwargs: dict[str, Any] = {"id": name}
        applied = None if not effort or str(effort).strip().lower() in ("none", "off") else effort
        if applied:
            kwargs["reasoning_effort"] = applied
        use_responses = (
            _api["responses"]
            or (os.environ.get("MODEL_API") or "chat").strip().lower() == "responses"
            or bool(applied)
        )
        if use_responses:
            return _responses_model(**kwargs)
        from agno.models.openai import OpenAIChat

        return OpenAIChat(**kwargs)
    return mid  # other providers: let Agno resolve "provider:model"


def _model(effort: Optional[str]):
    return _openai_model(app_settings.assistant_model(), effort)


def _pending_state(saved: dict) -> dict:
    for m in reversed(saved.get("messages") or []):
        if m["role"] == "assistant":
            state = (m.get("meta") or {}).get("state")
            return state if isinstance(state, dict) else {}
    return {}


def _history(saved: dict) -> list[Message]:
    return [
        Message(role=m["role"], content=m["content"])
        for m in saved.get("messages") or []
        if m["role"] in ("user", "assistant") and m.get("content")
    ]


def _packs_for(settings: Any, pinned: str) -> list[DomainPack]:
    general = get_pack("general")
    if pinned and pinned != AUTO:
        picked = [p for p in (get_pack(pinned), general) if p is not None]
        if picked:
            return list(dict.fromkeys(picked))
    packs = enabled_packs(settings.enabled_domains)
    if general is not None and general not in packs:
        packs.append(general)
    return packs


def _guardrail_audit(ctx: Any, scope: Any, session_id: str, status: str, domain: str = "") -> None:
    try:
        from freedombot.audit.events import log
        from freedombot.types import AuditEventType

        fields: dict[str, Any] = {"session_id": session_id, "status": status, "execution_mode": str(scope.mode)}
        if domain:
            fields["domain"] = domain
        log(AuditEventType.GUARDRAIL_TRIGGERED, ctx=ctx, **fields)
    except Exception:  # noqa: BLE001 - auditing must not break the reply
        pass
