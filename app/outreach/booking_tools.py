"""Cal.com bookings: move or cancel a booking that already exists.

The person books their own slot after qualifying; the team never books for them. Reading bookings is
``query_records`` (dataset bookings). Cancelling asks the user for one yes first, because Cal.com
emails the guest. Times are Chicago time, and replies say so.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from acquisition.integrations.calcom.client import CalcomClient, CalcomError
from acquisition.models.campaign import CampaignProspect
from acquisition.models.prospect import Prospect
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.booking_service import BookingService
from app.outreach import chicago, lookup, records
from app.outreach.confirm import action_key
from app.outreach.toolkit import Call, ToolError, tool

DEFAULT_MINUTES = 30


def _cal(call: Call) -> CalcomClient:
    client = CalcomClient(db=call.db)
    if not client.configured:
        raise ToolError("Cal.com is not connected. An admin connects it on the Integrations page.")
    return client


def _confirmed_booking(call: Call, who: str) -> tuple[Prospect, CampaignProspect]:
    p = lookup.person(call, who)
    link = records.link_row(call.db, p.id)
    if link is None or (link.booking_status or "").lower() != "confirmed" or not link.calcom_booking_uid:
        raise ToolError(f"{p.executive_name} has no confirmed booking to change.")
    return p, link


def _future(start: str) -> datetime:
    try:
        moment = chicago.parse_moment(start)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if moment <= datetime.now(timezone.utc):
        raise ToolError(f"{chicago.fmt_time(moment)} is in the past. Ask the user for a future time.")
    return moment


def _settings(call: Call):
    return AcquisitionJourneyService(call.db).get_settings()


def _record(call: Call, link: CampaignProspect, booking: dict, start: datetime, minutes: int) -> None:
    """Save a Cal.com booking on the person exactly as the Cal.com webhook would."""
    end = start + timedelta(minutes=minutes)
    BookingService(call.db).confirm_booking(
        link,
        calcom_booking_uid=booking["uid"],
        calcom_booking_id=booking.get("id"),
        meeting_start=start,
        meeting_end=end,
        meeting_location="Cal.com Video" if not booking.get("meetingUrl") else None,
        meeting_join_url=booking.get("meetingUrl"),
    )


def _cal_error(exc: CalcomError) -> ToolError:
    return ToolError(f"Cal.com said: {exc.message}. Nothing was changed.")


# ------------------------------------------------------------------------------------------------ reschedule
@tool("reschedule_booking", "who", "start")
def reschedule_booking(call: Call, who: str = "", start: str = "") -> str:
    """Move a confirmed booking to a new Chicago-time slot; Cal.com emails the guest."""
    p, link = _confirmed_booking(call, who)
    when = _future(start)
    old = chicago.fmt_time(link.meeting_start)
    minutes = int((link.meeting_end - link.meeting_start).total_seconds() // 60) if link.meeting_end and link.meeting_start \
        else (_settings(call).calcom_event_duration_minutes or DEFAULT_MINUTES)
    try:
        booking = _cal(call).reschedule_booking(link.calcom_booking_uid, start_utc=chicago.to_utc_iso(when),
                                                reason="Rescheduled by the team")
    except CalcomError as exc:
        raise _cal_error(exc) from exc
    _record(call, link, booking, when, minutes)
    return f"Moved {p.executive_name} from {old} to {chicago.fmt_time(when)} {chicago.TZ_LABEL}."


# ------------------------------------------------------------------------------------------------ cancel
@tool("cancel_booking", "who", "approval_evidence")
def cancel_booking(call: Call, who: str = "", approval_evidence: str = "") -> str:
    """Cancel a confirmed booking; Cal.com emails the guest."""
    p, link = _confirmed_booking(call, who)
    old = chicago.fmt_time(link.meeting_start)
    key = action_key("cancel_booking", {"id": str(p.id), "uid": link.calcom_booking_uid})
    stop = call.confirmed(key, approval_evidence,
                          f"Cancel {p.executive_name}'s call on {old} {chicago.TZ_LABEL}? Cal.com emails them the cancellation.")
    if stop:
        return stop
    try:
        _cal(call).cancel_booking(link.calcom_booking_uid, reason="Cancelled by the team")
    except CalcomError as exc:
        raise _cal_error(exc) from exc
    BookingService(call.db).mark_cancelled(link)
    call.db.commit()
    return f"Cancelled {p.executive_name}'s call on {old} {chicago.TZ_LABEL}."
