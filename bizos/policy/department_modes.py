"""
Per-Person Department Access
============================

An admin gives each person one or more departments (the domain packs: General, Sales, Planning,
Finance, Legal, Operations, Intake, Strategy, Brand, plus Human Resources) and, for each one, a role:

* **Advise**: just answers. Nothing is drafted, sent or changed.
* **Draft**: prepares emails, CRM changes and other actions for the person to review.
* **Approve**: sending email, CRM writes and spending money wait for a human to approve.
* **Auto**: routine work runs by itself.

A person starts with one department; an admin can add more later, each with its own role. Only
admins can change any of this (the admin tools and API routes are admin-only). This is the only
per-person limit on sending email and CRM writes; the old per-person CRM level is folded into it by
:func:`fold_crm_level` when the control plane migrates.

Two more choices sit in the same list but are levels, not departments with a role: **Employee**
(asks and looks things up, changes nothing) and **Admin** (every department, full access).

It only ever narrows. A write is held to the most permissive role among the person's departments
whose pack contains that tool; a write that belongs to none of their departments is held to Advise.
The workspace mode, pack ceilings, per-tool modes, the person's level and every guardrail still
apply on top, so **Auto** adds no extra limit but can never make anything run sooner than the
workspace allows. A person with no departments recorded (accounts made before this existed, and
admins, who have every department) gets no extra limit from here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping, Optional

from bizos.domains.catalog import DOMAIN_CATALOG, DOMAIN_NAMES
from bizos.types import ExecutionMode, mode_permissiveness

#: Departments an admin can give a person, in the order the admin screen lists them. Human Resources
#: has no pack of its own; it covers the HR tools and brings the HR and salary data.
DEPARTMENTS: tuple[str, ...] = (*DOMAIN_NAMES, "hr")
DEPARTMENT_LABELS: dict[str, str] = {**{e.name: e.title for e in DOMAIN_CATALOG}, "hr": "Human Resources"}

#: Listed beside the departments, but they are levels: no role inside them, and choosing one replaces
#: the person's departments. Employee is the view-only level; Admin has every department.
LEVEL_CHOICES: dict[str, str] = {
    "employee": "Asks and looks things up. Cannot change anything.",
    "admin": "Every department, full access.",
}

#: The four roles, and the mode each one holds the person's changes in that department to.
MODES: dict[str, ExecutionMode] = {
    "advise": ExecutionMode.ADVISE,
    "draft": ExecutionMode.DRAFT,
    "approve": ExecutionMode.WAIT_FOR_APPROVAL,
    "auto": ExecutionMode.AUTO_WITHIN_SCOPE,
}
MODE_LABELS: dict[str, str] = {"advise": "Advise", "draft": "Draft", "approve": "Approve", "auto": "Auto"}
MODE_MEANINGS: dict[str, str] = {
    "advise": "Just answers. Nothing is drafted, sent or changed.",
    "draft": "Prepares emails, CRM changes and other actions for the person to review. Nothing is sent.",
    "approve": "Sending email, CRM writes and spending money wait for a human to approve.",
    "auto": "Routine work runs by itself. Risky actions still wait for approval.",
}
#: The role a department gets when none is named.
DEFAULT_MODE = "approve"

#: The restricted data tags that come with a department, if it has any.
DEPARTMENT_TAGS: dict[str, tuple[str, ...]] = {
    "finance": ("finance",),
    "legal": ("legal",),
    "operations": ("ops",),
    "sales": ("sales",),
    "hr": ("hr", "salary"),
}

#: Rule codes this module produces. An admin's "skip approval" option never lifts them.
DEPARTMENT_RULES = frozenset({"PERSON_DEPT_ADVISE", "PERSON_DEPT_DRAFT", "PERSON_DEPT_APPROVAL"})

_MODE_ALIASES = {
    "advice": "advise",
    "approval": "approve",
    "needs_approval": "approve",
    "wait_for_approval": "approve",
    "auto_within_scope": "auto",
    "automatic": "auto",
}


def parse_department(value: Any) -> str:
    """A department name from an admin or the assistant. Raises ``ValueError`` for anything else."""
    name = str(value or "").strip().casefold()
    name = name.replace("_", " ")
    name = {"ops": "operations", "operation": "operations", "human resources": "hr",
            "human resource": "hr"}.get(name, name)
    if name not in DEPARTMENTS:
        raise ValueError(f"{value!r} is not a department. Use one of: {', '.join(DEPARTMENTS)}")
    return name


def parse_mode(value: Any) -> str:
    """A role name (advise, draft, approve, auto). Raises ``ValueError`` for anything else."""
    name = str(value or "").strip().casefold().replace(" ", "_")
    name = _MODE_ALIASES.get(name, name)
    if name not in MODES:
        raise ValueError(f"{value!r} is not a department role. Use one of: {', '.join(MODES)}")
    return name


def stored_departments(raw: Any) -> dict[str, str]:
    """Clean a stored ``{department: role}`` map: known departments and roles only."""
    out: dict[str, str] = {}
    for department, mode in dict(raw or {}).items():
        if department in DEPARTMENTS and mode in MODES:
            out[str(department)] = str(mode)
    return out


@lru_cache(maxsize=None)
def _departments_for(tool: str) -> frozenset[str]:
    from bizos.domains.packs import PACKS
    from bizos.rbac.registry import CATEGORY_HR, CATEGORY_OUTREACH, get_spec

    spec = get_spec(tool)
    if spec is None:
        return frozenset()
    found = {p.name for p in PACKS if any(s.name == tool for s in p.tool_specs())}
    if not found and spec.category == CATEGORY_OUTREACH:
        # Prospecting and outreach mail belong to no pack; they are Sales work.
        found.add("sales")
    if spec.category == CATEGORY_HR or spec.data_area in ("hr", "salary"):
        found.add("hr")
    return frozenset(found)


def tool_departments(spec: Any) -> frozenset[str]:
    """The departments whose pack contains this tool."""
    return _departments_for(str(getattr(spec, "name", "")))


def mode_for_tool(departments: Mapping[str, str], spec: Any) -> Optional[ExecutionMode]:
    """The mode a person's departments hold this tool to, or ``None`` for no extra limit.

    No departments recorded means no extra limit. Otherwise the most permissive role among the
    departments that contain the tool wins; a tool in none of them is held to Advise.
    """
    if not departments:
        return None
    covering = tool_departments(spec)
    held = [MODES[m] for d, m in departments.items() if d in covering and m in MODES]
    if not held:
        return ExecutionMode.ADVISE
    best = max(held, key=mode_permissiveness)
    return None if best == ExecutionMode.AUTO_WITHIN_SCOPE else best


def choices() -> dict[str, list[dict[str, str]]]:
    """Departments, the Employee and Admin levels, and the roles with their meanings, for the admin screen."""
    return {
        "departments": [{"value": d, "label": DEPARTMENT_LABELS.get(d, d)} for d in DEPARTMENTS],
        "levels": [{"value": k, "label": k.title(), "meaning": v} for k, v in LEVEL_CHOICES.items()],
        "modes": [{"value": m, "label": MODE_LABELS[m], "meaning": MODE_MEANINGS[m]} for m in MODES],
    }


@lru_cache(maxsize=None)
def crm_departments() -> frozenset[str]:
    """Departments whose pack contains a CRM write."""
    from bizos.rbac.registry import CATEGORY_CRM, TOOL_SPECS

    return frozenset(d for s in TOOL_SPECS if s.category == CATEGORY_CRM and s.write for d in tool_departments(s))


def _stricter(a: str, b: str) -> str:
    return min(a, b, key=lambda m: mode_permissiveness(MODES[m]))


def fold_crm_level(departments: Mapping[str, str], crm_mode: Optional[str], job_role: Optional[str]) -> dict[str, str]:
    """A person's departments once their old per-person CRM level (Needs approval / Draft only) is folded in.

    The result is never less strict than before. Each held department with CRM writes is capped at that
    level. A person with no departments gets their job role's department (General if it has none) at that
    level, and every other department's writes become Advise, which is stricter than before.
    """
    cap = {"DRAFT": "draft", "WAIT_FOR_APPROVAL": "approve"}.get(str(crm_mode or "").upper())
    held = stored_departments(departments)
    if cap is None:
        return held
    if not held:
        own = str(job_role or "").casefold()
        return {own if own in DEPARTMENTS else "general": cap}
    return {d: _stricter(m, cap) if d in crm_departments() else m for d, m in held.items()}
