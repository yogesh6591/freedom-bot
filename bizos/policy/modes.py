"""
Execution Mode Resolution
=========================

The effective mode for a run is the **most restrictive** of:

1. the workspace default (``ClientSettings.default_execution_mode``),
2. the domain pack's override, if it declares one, and
3. the per-request override, if one was supplied.

A request may only narrow. Allowing a request to widen would let anyone who can
reach the API put the workspace into AUTO_WITHIN_SCOPE, which is exactly the
"depend only on prompts for security" failure §28 forbids — the request payload
is not a trusted source of authority.
"""

from __future__ import annotations

from typing import Optional

from bizos.types import ExecutionMode, mode_permissiveness


def narrowest(*modes: Optional[ExecutionMode]) -> ExecutionMode:
    """The most restrictive of the supplied modes, ignoring ``None``."""
    present = [m for m in modes if m is not None]
    if not present:
        return ExecutionMode.ADVISE
    return min(present, key=mode_permissiveness)


def resolve_mode(
    workspace_default: ExecutionMode,
    *,
    domain_override: Optional[ExecutionMode] = None,
    requested: Optional[ExecutionMode] = None,
) -> ExecutionMode:
    """The effective execution mode for one run."""
    return narrowest(workspace_default, domain_override, requested)


def describe(mode: ExecutionMode) -> str:
    """One-line description used in the agent's system prompt and in the UI."""
    return {
        ExecutionMode.ADVISE: (
            "ADVISE — you may analyze, answer and recommend. You cannot change anything "
            "in any system; every tool you hold is read-only."
        ),
        ExecutionMode.DRAFT: (
            "DRAFT — you may prepare actions (drafts, proposals, prepared updates) for a "
            "human to review. Nothing is sent or written to an external system."
        ),
        ExecutionMode.WAIT_FOR_APPROVAL: (
            "WAIT FOR APPROVAL — you may propose real actions. Each one enters the approval "
            "queue and only runs after an approver accepts it."
        ),
        ExecutionMode.AUTO_WITHIN_SCOPE: (
            "AUTO WITHIN SCOPE — you may carry out low-risk actions that pass every policy "
            "check. Anything above the configured risk or limits still goes to approval."
        ),
    }[mode]
