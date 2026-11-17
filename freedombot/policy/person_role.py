"""
Per-Person Role and Department Access
=====================================

Each person has **one role**, set by an admin, that applies to everything they do:

* **Advise**: just answers. Nothing is drafted, sent or changed.
* **Draft**: prepares emails, CRM changes and other actions for the person to review.
* **Approve**: sending email, CRM writes and spending money wait for a human to approve.
* **Auto**: routine work runs by itself.

Their **data access** tags say which departments they work in (General, Sales, Planning, Finance,
Legal, Operations, Intake, Strategy, Brand, HR) and which apps they can use (CRM, Gmail, Calendar).
A person can hold several. A write that belongs only to departments they do not have is refused.

It only ever narrows. The workspace mode, pack ceilings, per-tool modes, the person's level and every
guardrail still apply on top, so **Auto** adds no limit of its own but never makes anything run sooner
than the workspace allows. Admins have every department with full access. A person with no role
recorded (accounts made before roles existed) gets no extra limit from here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional

from freedombot.domains.catalog import DOMAIN_CATALOG, DOMAIN_NAMES
from freedombot.types import ExecutionMode, mode_permissiveness

#: The four roles, and the mode each one holds a person's writes to.
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
#: The role a person gets when none is named.
DEFAULT_MODE = "approve"

#: Department -> the data access tag that gives it. HR also brings salary data.
DEPARTMENTS: tuple[str, ...] = (*DOMAIN_NAMES, "hr")
DEPARTMENT_TAG: dict[str, str] = {d: d for d in DEPARTMENTS} | {"operations": "ops"}
DEPARTMENT_LABELS: dict[str, str] = {**{e.name: e.title for e in DOMAIN_CATALOG}, "hr": "HR"}
_TAG_DEPARTMENT: dict[str, str] = {tag: d for d, tag in DEPARTMENT_TAG.items()}
#: Extra tags that come with a department tag.
TAG_COMPANIONS: dict[str, tuple[str, ...]] = {"hr": ("salary",)}

#: Rule codes this module produces. An admin's "skip approval" option never lifts them.
ROLE_RULES = frozenset({"PERSON_ROLE_ADVISE", "PERSON_ROLE_DRAFT", "PERSON_ROLE_APPROVAL", "PERSON_DEPARTMENT"})

_MODE_ALIASES = {
    "advice": "advise",
    "advisor": "advise",
    "drafter": "draft",
    "approval": "approve",
    "approver": "approve",
    "needs_approval": "approve",
    "wait_for_approval": "approve",
    "auto_within_scope": "auto",
    "automatic": "auto",
}


def parse_mode(value: Any) -> str:
    """A role name (advise, draft, approve, auto). Raises ``ValueError`` for anything else."""
    name = str(value or "").strip().casefold().replace(" ", "_")
    name = _MODE_ALIASES.get(name, name)
    if name not in MODES:
        raise ValueError(f"{value!r} is not a role. Use one of: {', '.join(MODES)}")
    return name


def stored_mode(raw: Any) -> Optional[str]:
    """A stored role, or ``None`` when nothing valid is stored."""
    value = str(raw or "").strip()
    return value if value in MODES else None


def departments_of(tags: Iterable[str]) -> frozenset[str]:
    """The departments a person's data access tags give them."""
    return frozenset(_TAG_DEPARTMENT[t] for t in tags if t in _TAG_DEPARTMENT)


@lru_cache(maxsize=None)
def _departments_for(tool: str) -> frozenset[str]:
    from freedombot.domains.packs import PACKS
    from freedombot.rbac.registry import CATEGORY_HR, CATEGORY_OUTREACH, get_spec

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
    """The departments whose work this tool is: the packs that contain it."""
    return _departments_for(str(getattr(spec, "name", "")))


def choices() -> dict[str, list[dict[str, str]]]:
    """The roles with their meanings and the departments, for the admin screen."""
    return {
        "modes": [{"value": m, "label": MODE_LABELS[m], "meaning": MODE_MEANINGS[m]} for m in MODES],
        "departments": [
            {"value": DEPARTMENT_TAG[d], "label": DEPARTMENT_LABELS.get(d, d)} for d in DEPARTMENTS
        ],
    }


# --------------------------------------------------------------------------- migration from earlier shapes


def _strictest(modes: Iterable[str]) -> Optional[str]:
    present = [m for m in modes if m in MODES]
    return min(present, key=lambda m: mode_permissiveness(MODES[m])) if present else None


def fold_legacy(
    department_modes: Any, crm_mode: Optional[str], job_role: Optional[str], tags: Iterable[str]
) -> tuple[Optional[str], list[str]]:
    """One role and the data tags for a person stored in an earlier shape. Never less strict.

    Earlier shapes: a role per department (``{"sales": "auto", "planning": "draft"}``) and a per-person
    CRM level (``DRAFT`` / ``WAIT_FOR_APPROVAL``). The role becomes the strictest of them; each held
    department becomes a data tag. A CRM level with no departments keeps the job role's department
    (General if none), so every other department's writes are refused, which is stricter than before.
    """
    if isinstance(department_modes, str):
        department_modes = json.loads(department_modes or "{}")
    held = {str(d): str(m) for d, m in dict(department_modes or {}).items() if d in DEPARTMENT_TAG and m in MODES}
    cap = {"DRAFT": "draft", "WAIT_FOR_APPROVAL": "approve"}.get(str(crm_mode or "").upper())
    out = set(tags)
    if not held and cap:
        own = str(job_role or "").casefold()
        held = {own if own in DEPARTMENT_TAG else "general": cap}
    for department in held:
        tag = DEPARTMENT_TAG[department]
        out |= {tag, *TAG_COMPANIONS.get(tag, ())}
    mode = _strictest([*held.values(), *([cap] if cap else [])])
    return mode, sorted(out)


def describe(tags: Iterable[str]) -> str:
    """"Sales, Finance": the departments a person's tags give them, for messages."""
    held = departments_of(tags)
    return ", ".join(DEPARTMENT_LABELS.get(d, d) for d in DEPARTMENTS if d in held)


def mode_of(value: Optional[str]) -> Optional[ExecutionMode]:
    return MODES.get(value or "")


def label(value: Optional[str]) -> Optional[str]:
    return MODE_LABELS.get(value or "")


def companions(tags: Mapping[str, Any] | Iterable[str]) -> set[str]:
    """``tags`` plus the tags that come with them (HR brings salary)."""
    out = set(tags)
    for tag in list(out):
        out |= set(TAG_COMPANIONS.get(tag, ()))
    return out
