"""Cal.com admin helpers for Integrations + Acquisition Calendar."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from acquisition.integrations.calcom.client import CalcomClient, CalcomError
from acquisition.services.calcom_service import CalcomService

logger = logging.getLogger(__name__)


def calcom_admin_context(
    settings_row,
    db: Session | None = None,
    *,
    include_events: bool = True,
) -> dict[str, Any]:
    """Status (+ optional event types). Full API key never returned."""
    service = CalcomService(db=db)
    summary = service.get_summary()
    ctx: dict[str, Any] = {
        "calcom": summary,
        "calcom_key_configured": summary.api_key_configured,
        "calcom_account": None,
        "calcom_event_types": [],
        "calcom_error": None,
        "calcom_settings": settings_row,
    }
    if not summary.api_key_configured:
        ctx["calcom_error"] = (
            "Add a Cal.com API key below to connect."
            if not include_events
            else "Connect Cal.com under Integrations first."
        )
        return ctx
    try:
        client = CalcomClient(db=db)
        me = client.get_me()
        ctx["calcom_account"] = me
        if include_events:
            username = me.get("username") or getattr(settings_row, "calcom_username", None)
            ctx["calcom_event_types"] = client.list_event_types(username=username)
    except CalcomError as exc:
        ctx["calcom_error"] = exc.message
        logger.warning("Cal.com admin context failed: %s", exc.message)
    return ctx


def apply_calcom_event_selection(
    *,
    row,
    event_type_id: str,
    db: Session,
) -> None:
    """Update journey settings from an existing Cal.com event type id."""
    client = CalcomClient(db=db)
    me = client.get_me()
    username = me.get("username") or getattr(row, "calcom_username", None)
    events = client.list_event_types(username=username)
    selected = next((e for e in events if str(e.get("id")) == str(event_type_id).strip()), None)
    if selected is None:
        raise CalcomError("Selected event type was not found", http_status=404)
    row.calcom_event_type_id = str(selected["id"])
    row.calcom_event_slug = selected.get("slug")
    row.calcom_event_title = selected.get("title")
    duration = selected.get("lengthInMinutes")
    row.calcom_event_duration_minutes = int(duration) if duration is not None else None
    booking = selected.get("bookingUrl")
    if not booking and username and selected.get("slug"):
        booking = f"https://cal.com/{username}/{selected['slug']}"
    row.calcom_booking_url = booking
    row.calcom_username = username
    if booking:
        row.calendar_url = booking
    # Prospect is the sole booker; hide Cal.com "Add guests" / "Add another".
    # CC invitees are still attached via guests= on the embed URL.
    try:
        client.ensure_event_booker_defaults(selected["id"])
    except CalcomError as exc:
        logger.warning(
            "Could not apply Cal.com booker defaults on event %s: %s",
            selected.get("id"),
            exc.message,
        )
    try:
        client.ensure_time_format(12)
    except CalcomError as exc:
        logger.warning("Could not set Cal.com host timeFormat to 12h: %s", exc.message)
