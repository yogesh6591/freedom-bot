"""
Per-Person App Access
=====================

An admin can hold one person to **Needs approval** or **Draft only** for the
changes they make in one connected app (the CRM today), from the Users page.

It only ever narrows. The workspace mode, the per-tool modes on the Tools tab,
the person's role and every guardrail still apply on top, so this setting can
make a change wait or stay a draft but can never let it run sooner. **Direct
changes** means no extra limit from this setting; whether a change then runs
straight away is still decided by the workspace mode and the Tools tab.

Releasing an approved action is the approval itself, so the approver's own level
is not applied to it.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from bizos.types import APP_AREAS, ExecutionMode

#: Apps an admin can set a per-person level for.
PERSON_APP_AREAS: tuple[str, ...] = ("crm",)

#: The levels the Users page offers, and the mode each one narrows to.
LEVELS: dict[str, Optional[ExecutionMode]] = {
    "direct": None,
    "approval": ExecutionMode.WAIT_FOR_APPROVAL,
    "draft": ExecutionMode.DRAFT,
}
LEVEL_LABELS = {"direct": "Direct changes", "approval": "Needs approval", "draft": "Draft only"}
AREA_LABELS = {"crm": "CRM", "email": "Gmail", "calendar": "Calendar"}

#: Rule codes this module produces. An admin's "skip approval" option never lifts them.
PERSON_RULES = frozenset({"PERSON_APP_DRAFT", "PERSON_APP_APPROVAL"})

#: Connected-app actions (integration_*) name their app in the action slug.
_TOOLKIT_AREAS = (("GOOGLECALENDAR_", "calendar"), ("HUBSPOT_", "crm"), ("GMAIL_", "email"))


def app_area(spec: Any, arguments: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """The connected app a tool call changes, if any: its own area, or the app a connected-app action names."""
    area = getattr(spec, "data_area", None)
    if area in APP_AREAS:
        return area
    if str(getattr(spec, "name", "")).startswith("integration_"):
        action = str((arguments or {}).get("action") or "").strip().upper()
        for prefix, toolkit_area in _TOOLKIT_AREAS:
            if action.startswith(prefix):
                return toolkit_area
    return None


def parse_level(value: Any) -> str:
    """A level name from the API. Raises ``ValueError`` for anything else."""
    level = str(value or "").strip().casefold()
    if level not in LEVELS:
        raise ValueError(f"{value!r} is not an access level. Use one of: {', '.join(LEVELS)}")
    return level


def level_of(mode: Optional[str]) -> str:
    """The Users-page level for a stored mode (nothing stored means Direct changes)."""
    for level, narrowed in LEVELS.items():
        if narrowed is not None and str(narrowed) == str(mode or ""):
            return level
    return "direct"


def stored_modes(raw: Any) -> dict[str, str]:
    """Clean a stored ``{area: mode}`` map: known areas and narrowing modes only."""
    allowed = {str(m) for m in LEVELS.values() if m is not None}
    out: dict[str, str] = {}
    for area, mode in dict(raw or {}).items():
        if area in PERSON_APP_AREAS and str(mode) in allowed:
            out[str(area)] = str(mode)
    return out
