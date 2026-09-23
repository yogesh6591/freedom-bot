"""
Registry Consistency Check
==========================

Run at startup and in the test suite. Catches the two failure modes that would
quietly break the "one central place decides permissions" guarantee:

* a **tool with an implementation but no spec** — it would bypass the policy
  engine entirely, since ``guarded_call`` denies what it cannot look up;
* a **spec marked implemented with no effect function** — it would appear on the
  agent's surface and then fail at call time.

Also re-asserts the §19 rule that no registered tool is a general-purpose executor.
"""

from __future__ import annotations

from bizos.rbac.registry import TOOL_SPECS, assert_no_generic_tools


def load_all_tools() -> None:
    """Import every tool module so the effect registry is fully populated."""
    from bizos.tools import accounting, calendar, crm, email, memory, review  # noqa: F401


def verify_registry() -> dict[str, list[str]]:
    """Return the inconsistencies found. Empty lists mean the registry is sound."""
    load_all_tools()
    from bizos.tools.base import EFFECTS

    assert_no_generic_tools()

    spec_names = {s.name for s in TOOL_SPECS}
    implemented = {s.name for s in TOOL_SPECS if s.implemented}

    return {
        "effects_without_spec": sorted(set(EFFECTS) - spec_names),
        "implemented_without_effect": sorted(implemented - set(EFFECTS)),
        "declared_not_implemented": sorted(spec_names - implemented),
    }


def assert_registry_sound() -> None:
    """Raise if the registry and the implementations disagree."""
    report = verify_registry()
    problems = {k: v for k, v in report.items() if v and k != "declared_not_implemented"}
    if problems:
        raise AssertionError(f"Tool registry is inconsistent: {problems}")
