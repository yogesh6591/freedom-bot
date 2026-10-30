"""Confirm Cal.com bookings and drive the After Booking experience."""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.models.campaign import CampaignProspect, JourneyStage
from acquisition.models.replies import ProviderEvent

logger = logging.getLogger(__name__)

BOOKING_STATUS_NOT_BOOKED = "not_booked"
BOOKING_STATUS_CONFIRMED = "confirmed"
BOOKING_STATUS_CANCELLED = "cancelled"

FB_TOKEN_META_KEYS = ("fb_token", "assessment_token", "freedombot_token")


class BookingAuthError(Exception):
    pass


class BookingServiceError(Exception):
    def __init__(self, message: str, *, http_status: int = 400, code: str = "booking_error") -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def extract_fb_token(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    for key in FB_TOKEN_META_KEYS:
        raw = metadata.get(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    # Nested / alternate shapes
    for key, val in metadata.items():
        if str(key).lower() in FB_TOKEN_META_KEYS and val:
            return str(val).strip()
    return None


def build_calendar_embed_url(
    *,
    booking_url: str,
    token: str,
    guest_name: str | None = None,
    guest_email: str | None = None,
    cc_emails: list[str] | None = None,
) -> str:
    """Append embed + Chicago timezone + guest prefill + success redirect.

    Prefill name/email so Cal.com confirmation mail goes to the prospect.
    Optional ``cc_emails`` are added as Cal.com guests so they also get the invite.
    """
    base = (booking_url or "").strip()
    if not base:
        return ""
    settings = get_settings()
    success = ""
    try:
        from acquisition.services.assessment_tokens import PublicRedirectError, public_redirect_base

        public = public_redirect_base(settings=settings)
        success = f"{public}/a/{quote(token, safe='')}/booked"
    except PublicRedirectError:
        public = (settings.public_base_url or "").rstrip("/")
        if public:
            success = f"{public}/a/{quote(token, safe='')}/booked"
    from acquisition.timezones import platform_timezone_name

    tz = platform_timezone_name()
    sep = "&" if "?" in base else "?"
    parts = [
        "embed=true",
        f"timezone={quote(tz, safe='')}",
        "layout=month_view",
        # Prefer 12-hour slots in the booker when Cal.com honors the hint.
        "timeFormat=12",
        "lang=en",
        f"metadata[fb_token]={quote(token, safe='')}",
    ]
    if success:
        parts.append(f"successRedirectUrl={quote(success, safe='')}")
    name = (guest_name or "").strip()
    email = (guest_email or "").strip()
    if name:
        parts.append(f"name={quote(name, safe='')}")
    if email:
        parts.append(f"email={quote(email, safe='')}")

    primary = email.lower()
    seen: set[str] = {primary} if primary else set()
    for raw in cc_emails or []:
        cc = (raw or "").strip()
        if not cc or "@" not in cc:
            continue
        key = cc.lower()
        if key in seen:
            continue
        seen.add(key)
        # Cal.com accepts repeated guests= (and guest=) for additional invitees.
        parts.append(f"guests={quote(cc, safe='')}")

    return f"{base}{sep}{'&'.join(parts)}"


class BookingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def verify_calcom_signature(self, *, raw_body: bytes, signature: str | None) -> None:
        secret = (self.settings.calcom_webhook_secret or "").strip()
        if not secret:
            if self.settings.environment.strip().lower() == "production":
                raise BookingAuthError("Cal.com webhook secret is not configured")
            logger.warning("CALCOM_WEBHOOK_SECRET unset; accepting webhook without signature")
            return
        provided = (signature or "").strip()
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not provided or not hmac.compare_digest(provided, digest):
            raise BookingAuthError("Invalid Cal.com webhook signature")

    def is_booking_confirmed(self, link: CampaignProspect) -> bool:
        return (
            str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
            and link.booked_at is not None
        )

    def confirm_booking(
        self,
        link: CampaignProspect,
        *,
        calcom_booking_uid: str | None,
        calcom_booking_id: str | None = None,
        meeting_start: datetime | None = None,
        meeting_end: datetime | None = None,
        meeting_timezone: str | None = None,
        meeting_location: str | None = None,
        meeting_join_url: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently mark a prospect as booked. Same UID re-apply is a no-op update."""
        now = datetime.now(timezone.utc)
        uid = (calcom_booking_uid or "").strip() or None
        if uid and link.calcom_booking_uid and link.calcom_booking_uid != uid:
            # Same prospect, different booking (reschedule) — update details.
            pass
        if (
            self.is_booking_confirmed(link)
            and uid
            and link.calcom_booking_uid == uid
            and link.meeting_start == meeting_start
        ):
            return {"status": "already_confirmed", "token": link.assessment_token}

        if link.booked_at is None:
            link.booked_at = now
        link.booking_status = BOOKING_STATUS_CONFIRMED
        if uid:
            link.calcom_booking_uid = uid
        if calcom_booking_id:
            link.calcom_booking_id = str(calcom_booking_id).strip()
        if meeting_start is not None:
            link.meeting_start = meeting_start
        if meeting_end is not None:
            link.meeting_end = meeting_end
        # Platform is Chicago-based: always store the display timezone as the
        # app timezone (not the attendee's browser/Cal.com guest timezone).
        from acquisition.timezones import platform_timezone_name

        link.meeting_timezone = platform_timezone_name()[:64]
        if meeting_location is not None:
            link.meeting_location = (meeting_location or "").strip()[:512] or None
        if meeting_join_url is not None:
            link.meeting_join_url = (meeting_join_url or "").strip()[:1024] or None
        link.journey_stage = JourneyStage.BOOKED
        link.last_activity_at = now
        self.db.add(link)

        if event_id:
            self._record_provider_event(
                event_id=event_id,
                event_type="BOOKING_CREATED",
                link=link,
                summary=f"uid={uid or ''}",
            )

        self.db.commit()
        self.db.refresh(link)
        result = {
            "status": "confirmed",
            "token": link.assessment_token,
            "booked_at": link.booked_at.isoformat() if link.booked_at else None,
        }
        # Booking is committed. Brief generation must never undo confirmation.
        try:
            from acquisition.services.pre_call_brief_service import PreCallBriefService

            brief_result = PreCallBriefService(self.db).generate_after_booking_if_needed(link)
            result["pre_call_brief"] = brief_result.get("status")
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Pre-call brief hook failed after booking confirm: %s", type(exc).__name__
            )
            result["pre_call_brief"] = "failed"
        return result

    def handle_calcom_webhook(self, payload: dict[str, Any], *, event_id: str) -> dict[str, Any]:
        if self._already_processed(event_id):
            return {"status": "duplicate", "event_id": event_id}

        trigger = str(payload.get("triggerEvent") or payload.get("trigger") or "").strip()
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if trigger in {"BOOKING_CANCELLED", "BOOKING_REJECTED"}:
            return self._handle_cancel(body, event_id=event_id, trigger=trigger)
        if trigger and trigger not in {
            "BOOKING_CREATED",
            "BOOKING_RESCHEDULED",
            "BOOKING_CONFIRMED",
            "",
        }:
            self._record_provider_event(
                event_id=event_id, event_type=trigger or "ignored", link=None, summary="ignored"
            )
            self.db.commit()
            return {"status": "ignored", "trigger": trigger}

        token = extract_fb_token(body.get("metadata"))
        if not token:
            # Some payloads nest metadata under responses
            token = extract_fb_token(body.get("responses")) if isinstance(body.get("responses"), dict) else None
        if not token:
            self._record_provider_event(
                event_id=event_id,
                event_type=trigger or "BOOKING_CREATED",
                link=None,
                summary="missing_fb_token",
            )
            self.db.commit()
            return {"status": "unmapped", "reason": "missing_fb_token"}

        link = self.db.scalar(
            select(CampaignProspect).where(CampaignProspect.assessment_token == token)
        )
        if link is None:
            self._record_provider_event(
                event_id=event_id,
                event_type=trigger or "BOOKING_CREATED",
                link=None,
                summary=f"unknown_token",
            )
            self.db.commit()
            return {"status": "unmapped", "reason": "unknown_token"}

        uid = str(body.get("uid") or body.get("bookingUid") or "").strip() or None
        booking_id = body.get("bookingId") or body.get("id")
        join_url = None
        video = body.get("videoCallData")
        if isinstance(video, dict):
            join_url = video.get("url")
        if not join_url:
            join_url = body.get("meetingUrl") or body.get("videoCallUrl")
        location = body.get("location")
        if isinstance(location, dict):
            location = location.get("value") or location.get("joinUrl") or str(location)
        location_str = str(location).strip() if location else ""
        # Cal.com sends location *types* like "integrations:daily" — not a join URL.
        if location_str.lower().startswith(("http://", "https://")):
            if not join_url:
                join_url = location_str
            location_label = None
        elif location_str.lower() in {"integrations:daily", "daily", "cal-video", "calcom"}:
            location_label = "Cal.com Video"
        elif location_str:
            location_label = location_str
        else:
            location_label = None
        attendee_tz = None
        attendees = body.get("attendees")
        if isinstance(attendees, list) and attendees:
            first = attendees[0]
            if isinstance(first, dict):
                attendee_tz = first.get("timeZone") or first.get("timezone")
        organizer = body.get("organizer") if isinstance(body.get("organizer"), dict) else {}
        meeting_tz = attendee_tz or organizer.get("timeZone")
        start_raw = body.get("startTime") or body.get("start")
        end_raw = body.get("endTime") or body.get("end")

        return self.confirm_booking(
            link,
            calcom_booking_uid=uid,
            calcom_booking_id=str(booking_id) if booking_id is not None else None,
            meeting_start=_parse_dt(start_raw),
            meeting_end=_parse_dt(end_raw),
            meeting_timezone=meeting_tz,
            meeting_location=location_label,
            meeting_join_url=str(join_url).strip() if join_url else None,
            event_id=event_id,
        )

    def _handle_cancel(self, body: dict[str, Any], *, event_id: str, trigger: str) -> dict[str, Any]:
        uid = str(body.get("uid") or body.get("bookingUid") or "").strip()
        link = None
        if uid:
            link = self.db.scalar(
                select(CampaignProspect).where(CampaignProspect.calcom_booking_uid == uid)
            )
        if link is None:
            token = extract_fb_token(body.get("metadata"))
            if token:
                link = self.db.scalar(
                    select(CampaignProspect).where(CampaignProspect.assessment_token == token)
                )
        if link is not None:
            self.mark_cancelled(link)
        self._record_provider_event(
            event_id=event_id, event_type=trigger, link=link, summary=f"uid={uid}"
        )
        self.db.commit()
        return {"status": "cancelled", "token": link.assessment_token if link else None}

    def mark_cancelled(self, link: CampaignProspect) -> None:
        """Record that the booking was cancelled (the webhook and the chat both use this). Caller commits.

        Purchase fields are never cleared here: a Stripe payment stays even if the call is cancelled.
        """
        link.booking_status = BOOKING_STATUS_CANCELLED
        self.db.add(link)

    def _already_processed(self, event_id: str) -> bool:
        row = self.db.scalar(
            select(ProviderEvent).where(
                ProviderEvent.provider == "calcom",
                ProviderEvent.event_id == event_id,
            )
        )
        return row is not None

    def _record_provider_event(
        self,
        *,
        event_id: str,
        event_type: str,
        link: CampaignProspect | None,
        summary: str | None,
    ) -> None:
        if self._already_processed(event_id):
            return
        event = ProviderEvent(
            id=uuid.uuid4(),
            provider="calcom",
            event_id=event_id,
            event_type=event_type,
            campaign_id=link.campaign_id if link else None,
            campaign_prospect_id=link.id if link else None,
            payload_summary=(summary or "")[:500] or None,
        )
        try:
            with self.db.begin_nested():
                self.db.add(event)
                self.db.flush()
        except IntegrityError:
            pass
