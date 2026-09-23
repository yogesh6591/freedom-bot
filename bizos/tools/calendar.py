"""Calendar tool effects."""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.tools.base import RunScope, effect, risk_inputs


def _calendar(scope: RunScope):
    return get_connector("calendar", ctx=scope.ctx)


def _attendees(args: dict[str, Any]) -> list[str]:
    raw = args.get("attendees") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


@effect("calendar_list_events")
def calendar_list_events(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).list_events(
        start=args.get("start"), end=args.get("end"), limit=int(args.get("limit", 50))
    )


@effect("calendar_check_availability")
def calendar_check_availability(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).check_availability(
        start=str(args.get("start")),
        end=str(args.get("end")),
        slot_minutes=int(args.get("slot_minutes", 30)),
    )


@effect("calendar_create_event")
def calendar_create_event(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).create_event(
        title=str(args.get("title", "")),
        starts_at=str(args.get("starts_at")),
        ends_at=str(args.get("ends_at")),
        attendees=_attendees(args),
        description=str(args.get("description", "")),
        location=args.get("location"),
    )


@risk_inputs("calendar_create_event")
def _create_event_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Attendees are external recipients for the domain allowlist."""
    return {"recipients": tuple(_attendees(args))}


@effect("calendar_reschedule_event")
def calendar_reschedule_event(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).reschedule_event(
        str(args.get("event_id", "")),
        starts_at=str(args.get("starts_at")),
        ends_at=str(args.get("ends_at")),
    )


@effect("calendar_cancel_event")
def calendar_cancel_event(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).cancel_event(str(args.get("event_id", "")))
