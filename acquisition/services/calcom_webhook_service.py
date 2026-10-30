"""Register Cal.com booking webhooks against the public tunnel URL (dev + ops)."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.integrations.calcom.client import CalcomClient, CalcomError
from acquisition.models.campaign import CampaignProspect
from acquisition.services.booking_service import BookingService, extract_fb_token

logger = logging.getLogger(__name__)

BOOKING_TRIGGERS = [
    "BOOKING_CREATED",
    "BOOKING_RESCHEDULED",
    "BOOKING_CANCELLED",
    "BOOKING_REJECTED",
]
_INACTIVE_BOOKING_STATUSES = frozenset({"cancelled", "canceled", "rejected"})


def _booking_is_active(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    return status not in _INACTIVE_BOOKING_STATUSES


def _attendee_emails(row: dict[str, Any]) -> set[str]:
    emails: set[str] = set()
    attendees = row.get("attendees")
    if isinstance(attendees, list):
        for item in attendees:
            if isinstance(item, dict):
                email = str(item.get("email") or "").strip().lower()
                if email:
                    emails.add(email)
            elif isinstance(item, str) and "@" in item:
                emails.add(item.strip().lower())
    for key in ("email", "attendeeEmail"):
        raw = row.get(key)
        if isinstance(raw, str) and "@" in raw:
            emails.add(raw.strip().lower())
    responses = row.get("responses")
    if isinstance(responses, dict):
        nested = responses.get("email")
        if isinstance(nested, dict):
            nested = nested.get("value")
        if isinstance(nested, str) and "@" in nested:
            emails.add(nested.strip().lower())
    return emails


def _event_id_from_booking(row: dict[str, Any]) -> str:
    uid = str(row.get("uid") or "").strip()
    created = str(row.get("createdAt") or "")
    return f"BOOKING_CREATED:{uid}:{created}"[:240]


def _webhook_payload_from_booking(row: dict[str, Any], *, token: str) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
    metadata["fb_token"] = token
    created = str(row.get("createdAt") or "")
    return {
        "triggerEvent": "BOOKING_CREATED",
        "createdAt": created,
        "payload": {
            "uid": str(row.get("uid") or "").strip(),
            "bookingId": row.get("id"),
            "id": row.get("id"),
            "startTime": row.get("start") or row.get("startTime"),
            "endTime": row.get("end") or row.get("endTime"),
            "location": row.get("location") or row.get("meetingUrl"),
            "metadata": metadata,
            "attendees": row.get("attendees") or [],
            "videoCallData": {"url": row.get("meetingUrl")} if row.get("meetingUrl") else None,
        },
    }


class CalcomWebhookServiceError(Exception):
    def __init__(self, message: str, *, http_status: int = 422) -> None:
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class CalcomWebhookService:
    def __init__(self, db: Session, *, client: CalcomClient | None = None) -> None:
        self.db = db
        self.settings = get_settings()
        self.client = client or CalcomClient(db=db)

    def webhook_url(self) -> str:
        base = (self.settings.public_webhook_base_url or self.settings.public_base_url or "").rstrip(
            "/"
        )
        if not base:
            return ""
        return f"{base}/api/webhooks/calcom"

    def register_webhook(self, *, secret: str | None = None) -> dict[str, Any]:
        """Ensure a Cal.com user webhook points at our public /api/webhooks/calcom URL."""
        if not self.client.configured:
            raise CalcomWebhookServiceError("Cal.com is not connected (connect it in the chat app, Integrations)", http_status=503)
        url = self.webhook_url()
        if not url.startswith("https://"):
            raise CalcomWebhookServiceError(
                "PUBLIC_WEBHOOK_BASE_URL must be a public HTTPS origin "
                "(run `make tunnel-up` in local development).",
                http_status=503,
            )

        secret_value = (secret or self.settings.calcom_webhook_secret or "").strip()
        created_secret = False
        if not secret_value:
            secret_value = secrets.token_urlsafe(32)
            created_secret = True

        existing = self._find_matching(url)
        if existing:
            existing_secret = str(existing.get("secret") or "").strip()
            return {
                "status": "already_registered",
                "id": existing.get("id"),
                "subscriber_url": url,
                "secret": existing_secret or None,
                "secret_created": False,
                "message": "Cal.com webhook already points at this URL.",
            }

        # Drop stale FreedomBot tunnel webhooks so Cal.com doesn't keep dead URLs.
        self._prune_stale_freedombot_webhooks(keep_url=url)

        created = self.client.create_webhook(
            subscriber_url=url,
            triggers=BOOKING_TRIGGERS,
            secret=secret_value,
            active=True,
        )
        return {
            "status": "registered",
            "id": created.get("id"),
            "subscriber_url": url,
            "secret": secret_value,
            "secret_created": created_secret,
            "message": "Cal.com webhook registered.",
        }

    def reconcile_recent_bookings(self, *, take: int = 25) -> dict[str, Any]:
        """Apply real Cal.com bookings that carry metadata.fb_token (missed webhooks)."""
        if not self.client.configured:
            raise CalcomWebhookServiceError("Cal.com is not connected (connect it in the chat app, Integrations)", http_status=503)
        bookings = self.client.list_bookings(take=take, status=None)
        booking = BookingService(self.db)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in bookings:
            if not _booking_is_active(row):
                skipped.append({"uid": row.get("uid"), "reason": "inactive"})
                continue
            token = extract_fb_token(row.get("metadata"))
            if not token:
                skipped.append({"uid": row.get("uid"), "reason": "missing_fb_token"})
                continue
            result = booking.handle_calcom_webhook(
                _webhook_payload_from_booking(row, token=token),
                event_id=_event_id_from_booking(row),
            )
            applied.append({"uid": row.get("uid"), "token_prefix": token[:12], "result": result.get("status")})
        return {"status": "ok", "applied": applied, "skipped": skipped, "checked": len(bookings)}

    def reconcile_for_token(
        self, token: str, *, take: int = 25, uid: str | None = None
    ) -> dict[str, Any]:
        """Confirm this assessment token from Cal.com when the webhook never arrived."""
        wanted = (token or "").strip()
        if not wanted:
            return {"status": "ok", "applied": [], "skipped": [], "checked": 0}
        if not self.client.configured:
            raise CalcomWebhookServiceError(
                "Cal.com is not connected (connect it in the chat app, Integrations)",
                http_status=503,
            )
        link = self.db.scalar(
            select(CampaignProspect).where(CampaignProspect.assessment_token == wanted)
        )
        if link is None:
            return {"status": "ok", "applied": [], "skipped": [], "checked": 0}
        prospect_email = ""
        prospect = getattr(link, "prospect", None)
        if prospect is not None:
            prospect_email = str(getattr(prospect, "email", "") or "").strip().lower()

        booking = BookingService(self.db)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        checked = 0

        def consume(rows: list[dict[str, Any]]) -> bool:
            nonlocal checked
            for row in rows:
                checked += 1
                if not _booking_is_active(row):
                    skipped.append({"uid": row.get("uid"), "reason": "inactive"})
                    continue
                found = extract_fb_token(row.get("metadata")) or extract_fb_token(
                    row.get("responses")
                )
                emails = _attendee_emails(row)
                if not found and not emails:
                    row_uid = str(row.get("uid") or "").strip()
                    if row_uid:
                        try:
                            detail = self.client.get_booking_record(row_uid)
                            if isinstance(detail, dict):
                                row = {**row, **detail}
                        except CalcomError:
                            pass
                    found = extract_fb_token(row.get("metadata")) or extract_fb_token(
                        row.get("responses")
                    )
                    emails = _attendee_emails(row)
                if found and found != wanted:
                    skipped.append({"uid": row.get("uid"), "reason": "other_token"})
                    continue
                if not found:
                    if prospect_email and prospect_email in emails:
                        found = wanted
                    elif wanted_uid and str(row.get("uid") or "").strip() == wanted_uid:
                        owner = self.db.scalar(
                            select(CampaignProspect).where(
                                CampaignProspect.calcom_booking_uid == wanted_uid
                            )
                        )
                        owner_token = (
                            getattr(owner, "assessment_token", None) if owner is not None else None
                        )
                        if owner is None or owner_token == wanted:
                            found = wanted
                        else:
                            skipped.append({"uid": row.get("uid"), "reason": "other_token"})
                            continue
                    else:
                        skipped.append({"uid": row.get("uid"), "reason": "missing_fb_token"})
                        continue
                result = booking.handle_calcom_webhook(
                    _webhook_payload_from_booking(row, token=found),
                    event_id=_event_id_from_booking(row),
                )
                applied.append(
                    {
                        "uid": row.get("uid"),
                        "token_prefix": found[:12],
                        "result": result.get("status"),
                    }
                )
                if result.get("status") in {"confirmed", "already_confirmed"}:
                    return True
            return False

        wanted_uid = (uid or "").strip()
        if wanted_uid:
            try:
                detail = self.client.get_booking_record(wanted_uid)
            except CalcomError:
                detail = None
            if isinstance(detail, dict) and consume([detail]):
                return {
                    "status": "ok",
                    "applied": applied,
                    "skipped": skipped,
                    "checked": checked,
                }

        bookings = self.client.list_bookings(take=take, status=None)
        consume(bookings)
        return {
            "status": "ok",
            "applied": applied,
            "skipped": skipped,
            "checked": checked,
        }

    def _find_matching(self, url: str) -> dict[str, Any] | None:
        try:
            webhooks = self.client.list_webhooks()
        except CalcomError as exc:
            raise CalcomWebhookServiceError(exc.message, http_status=exc.http_status) from exc
        for row in webhooks:
            if str(row.get("subscriberUrl") or "").rstrip("/") == url.rstrip("/"):
                return row
        return None

    def _prune_stale_freedombot_webhooks(self, *, keep_url: str) -> None:
        try:
            webhooks = self.client.list_webhooks()
        except CalcomError:
            return
        keep = keep_url.rstrip("/")
        for row in webhooks:
            sub = str(row.get("subscriberUrl") or "").rstrip("/")
            if not sub.endswith("/api/webhooks/calcom"):
                continue
            if sub == keep:
                continue
            wid = row.get("id")
            if wid is None:
                continue
            try:
                self.client.delete_webhook(wid)
                logger.info("Removed stale Cal.com webhook id=%s url=%s", wid, sub)
            except CalcomError as exc:
                logger.warning("Could not delete stale Cal.com webhook id=%s: %s", wid, exc.message)
