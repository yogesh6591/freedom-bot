"""
Router Agent and Memory Prefetch (v2 pipeline)
==============================================

Agno wiring only. The routing rules (menu, bundles, visibility, clean-up) are plain Python in
:mod:`bizos.agents.routing`.

The router is a fast model with structured output (:class:`~bizos.agents.routing.Route`), no tools and
no memory. ``memory_search`` on the raw query runs in parallel with it, through the normal policy
check, so the executor prompt can carry company context.

Model: ``ROUTER_MODEL_ID`` (``provider:model``), falling back to ``MODEL_ID``.
"""

from __future__ import annotations

import contextvars
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

from bizos.agents.routing import Route
from bizos.tools.base import RunScope, ToolOutcome, guarded_call

log = logging.getLogger(__name__)

#: Used when the router fails: route nowhere and let the executor call load_tools.
FALLBACK_ROUTE = Route(domains=[], kind="task")


def router_model():
    from bizos import settings as app_settings

    mid = (os.environ.get("ROUTER_MODEL_ID") or "").strip() or app_settings.model_id()
    provider, _, name = mid.partition(":")
    if provider == "openai" and name:
        from agno.models.openai import OpenAIChat

        return OpenAIChat(id=name)
    return mid


def route_query(prompt: str, query: str, *, model: Any = None) -> Route:
    """Ask the router model for a :class:`Route`. Never raises; a failure routes nowhere."""
    from agno.agent import Agent

    agent = Agent(
        name="bizos-router",
        id="bizos-router",
        model=model or router_model(),
        instructions=prompt,
        output_schema=Route,
        add_history_to_context=False,
        enable_agentic_memory=False,
        telemetry=False,
        store_events=False,
    )
    try:
        content = agent.run(input=query).content
        if isinstance(content, Route):
            return content
        if isinstance(content, dict):
            return Route(**content)
        log.warning("v2 router returned no structured route: %r", content)
    except Exception as exc:  # noqa: BLE001 - a routing failure must not fail the request
        log.warning("v2 router failed: %s: %s", type(exc).__name__, exc)
    return FALLBACK_ROUTE


@dataclass
class Prefetched:
    route: Route
    #: The memory_search outcome, or None when memory_search is not visible to this user.
    memory: Optional[ToolOutcome]


def route_and_prefetch(
    route: Callable[[], Route], memory_scope: Optional[RunScope], query: str, *, limit: int = 5
) -> Prefetched:
    """Run the router and memory_search on the raw query at the same time.

    ``memory_scope`` is None when the user may not see memory_search. Each thread runs in a copy of the
    caller's context, so the tenant binding carries over.
    """
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v2-route") as pool:
        routed = pool.submit(contextvars.copy_context().run, route)
        memory = None
        if memory_scope is not None:
            memory = pool.submit(
                contextvars.copy_context().run,
                guarded_call, memory_scope, "memory_search", {"query": query, "limit": limit},
            )
        return Prefetched(route=routed.result(), memory=memory.result() if memory else None)
