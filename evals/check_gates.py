"""
Expected gates come from the policy check
=========================================

``gate_for(client, case, tool)`` is what the policy check gives ``tool`` for this case's user, workspace preset
and arguments (``expected_args``, completed by ``gate_args`` where the gate depends on the full call, such as
an email body): the policy dry-run (bizos/tools/dry_run.py) in the scope the live assistant binds the tool to,
in the case's mocked world. A merged tool's gate is the strictest gate of the old tools its call maps to.
``tests/test_eval_cases.py`` checks every case's ``expected_gates`` and ``expected_hidden`` against it, so a
case can never carry a hand-written gate.
"""

from __future__ import annotations

from typing import Any

ORDER = ["deny", "draft", "approval", "run"]

#: The old tool a merged tool maps to when its arguments do not say (for example, no decision given).
DEFAULT_OLD = {"admin_update_user": "admin_set_user_roles", "approvals_decide": "approvals_approve",
               "memory_add": "memory_add_fact", "youtube_my_channel": "youtube_my_videos",
               "youtube_video": "youtube_video_details"}


def _olds(tool: str, args: dict, scope_for) -> list[tuple[str, dict]]:
    from bizos.agents.merged_tools import MERGED, MergeArgumentError

    if tool not in MERGED:
        return [(tool, args)]
    try:
        calls = MERGED[tool].plan(scope_for, dict(args))
    except MergeArgumentError:
        calls = []
    return [(c.tool, c.args) for c in calls] or [(DEFAULT_OLD.get(tool, MERGED[tool].wraps[0]), {})]


def gates_and_visible(client, case: dict) -> tuple[dict[str, str], frozenset[str]]:
    """(gate per tool named in expected_gates or expected_hidden, every tool visible to the case's user)."""
    from pytest import MonkeyPatch

    import app.assistant.assistant as current
    from app.api.deps import bound
    from bizos.agents.persona import AUTO
    from bizos.agents.routing import visible_tools
    from bizos.tools.base import RunScope
    from bizos.tools.dry_run import bound_scope, dry_run_for_user
    from evals.run import context_for, patch_world, settings_for
    from evals.world import World

    fixtures = case.get("fixtures") or {}
    tools = set(case.get("expected_gates") or {}) | set(case.get("expected_hidden") or [])
    mp = MonkeyPatch()
    try:
        patch_world(mp, World(fixtures), fixtures)
        settings, ctx = settings_for(client, case), context_for(client, case)
        packs = current._packs_for(settings, AUTO)
        with bound(ctx):
            scope = RunScope(ctx=ctx, settings=settings)
            visible = visible_tools(scope, packs, outreach_allowed=ctx.is_admin)
            gates: dict[str, str] = {}
            for tool in tools:
                args: dict[str, Any] = {**((case.get("expected_args") or {}).get(tool) or {}),
                                        **((case.get("gate_args") or {}).get(tool) or {})}
                worst = "run"
                for old, old_args in _olds(tool, args, lambda t: bound_scope(scope, t, packs)):
                    gate = dry_run_for_user(scope, old, old_args, packs=packs).gate
                    worst = gate if ORDER.index(gate) < ORDER.index(worst) else worst
                gates[tool] = worst
    finally:
        mp.undo()
    return gates, visible


def problems(client, case: dict) -> list[str]:
    """Where the case disagrees with the policy check (empty when it agrees)."""
    gates, visible = gates_and_visible(client, case)
    out = [f"{tool}: case says {want}, the policy check gives {gates[tool]}"
           for tool, want in (case.get("expected_gates") or {}).items() if gates[tool] != want]
    out += [f"{tool}: expected hidden, but it is visible" for tool in case.get("expected_hidden") or []
            if tool in visible]
    return out
