"""
Policy Dry-Run
==============

What would happen if this user called this tool with these arguments: run, approval, draft or
deny. Nothing is executed, queued or audited.

It reuses :func:`bizos.tools.base.assess`, the same function :func:`~bizos.tools.base.guarded_call`
runs before it acts, so the dry-run cannot drift from the real policy check. It never
re-implements a rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from bizos.domains.packs import DomainPack
from bizos.rbac.registry import get_spec
from bizos.tools.base import RunScope, assess
from bizos.types import PolicyEffect

RUN, APPROVAL, DRAFT, DENY = "run", "approval", "draft", "deny"

GATE_FOR_EFFECT = {
    PolicyEffect.ALLOW: RUN,
    PolicyEffect.REQUIRE_APPROVAL: APPROVAL,
    PolicyEffect.DRAFT_ONLY: DRAFT,
    PolicyEffect.DENY: DENY,
}


@dataclass(frozen=True)
class DryRun:
    tool: str
    gate: str
    #: Why, in the words the policy check would give the user.
    reason: str
    #: The binding policy rule (e.g. "MODE_WAIT_FOR_APPROVAL"), or the check that refused.
    rule: str
    #: The pack (policy domain) the call was evaluated in.
    domain: str


def dry_run(scope: RunScope, tool: str, arguments: Optional[dict[str, Any]] = None) -> DryRun:
    """The gate for ``tool`` in exactly this scope (its domain and mode as given)."""
    checked = assess(scope, tool, arguments, record=False)
    rule = checked.decision.rule if checked.stage == "policy" else checked.stage
    return DryRun(tool, GATE_FOR_EFFECT[checked.effect], checked.message, rule, scope.domain)


def bound_scope(scope: RunScope, tool: str, packs: list[DomainPack]) -> RunScope:
    """The scope the live assistant binds ``tool`` to: the first pack that lets it through.

    Mirrors :func:`bizos.agents.surface.visible_specs_by_pack`. A tool in no pack (the
    campaign tools) keeps ``scope``. A tool no pack lets through is evaluated in its first
    pack, where it is denied.
    """
    from bizos.agents.surface import _visible, pack_scope

    spec = get_spec(tool)
    if spec is None:
        return scope
    first: Optional[RunScope] = None
    for pack in packs:
        if spec not in pack.tool_specs():
            continue
        candidate = pack_scope(scope, pack)
        first = first or candidate
        if spec.implemented and _visible(candidate, spec):
            return candidate
    return first or scope


def dry_run_for_user(
    scope: RunScope, tool: str, arguments: Optional[dict[str, Any]] = None, *, packs: list[DomainPack]
) -> DryRun:
    """The gate the user would get from the assistant for this call."""
    return dry_run(bound_scope(scope, tool, packs), tool, arguments)


def is_visible(scope: RunScope, tool: str, packs: list[DomainPack]) -> bool:
    """Whether the live assistant would show ``tool`` to this user (the policy check, no arguments).

    No preconditions are run, so a tool is never hidden just because arguments are missing.
    """
    from bizos.agents.surface import _visible

    spec = get_spec(tool)
    return spec is not None and spec.implemented and _visible(bound_scope(scope, tool, packs), spec)
