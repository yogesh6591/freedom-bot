"""Register Cal.com booking webhooks against the public tunnel URL (dev + ops)."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.integrations.calcom.client import CalcomClient, CalcomError
from acquisition.services.booking_service import BookingService, extract_fb_token

logger = logging.getLogger(__name__)

BOOKING_TRIGGERS = [
    "BOOKING_CREATED",
    "BOOKING_RESCHEDULED",
    "BOOKING_CANCELLED",
    "BOOKING_REJECTED",
]


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
            raise CalcomWebhookServiceError("Cal.com API key is not configured", http_status=503)
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
            raise CalcomWebhookServiceError("Cal.com API key is not configured", http_status=503)
        bookings = self.client.list_bookings(take=take, status=None)
        booking = BookingService(self.db)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in bookings:
            token = extract_fb_token(row.get("metadata"))
            if not token:
                skipped.append({"uid": row.get("uid"), "reason": "missing_fb_token"})
                continue
            uid = str(row.get("uid") or "").strip()
            created = str(row.get("createdAt") or "")
            event_id = f"BOOKING_CREATED:{uid}:{created}"[:240]
            # Build a webhook-shaped payload so confirm path stays single-source.
            payload = {
                "triggerEvent": "BOOKING_CREATED",
                "createdAt": created,
                "payload": {
                    "uid": uid,
                    "bookingId": row.get("id"),
                    "id": row.get("id"),
                    "startTime": row.get("start") or row.get("startTime"),
                    "endTime": row.get("end") or row.get("endTime"),
                    "location": row.get("location") or row.get("meetingUrl"),
                    "metadata": row.get("metadata") or {},
                    "attendees": row.get("attendees") or [],
                    "videoCallData": {"url": row.get("meetingUrl")} if row.get("meetingUrl") else None,
                },
            }
            result = booking.handle_calcom_webhook(payload, event_id=event_id)
            applied.append({"uid": uid, "token_prefix": token[:12], "result": result.get("status")})
        return {"status": "ok", "applied": applied, "skipped": skipped, "checked": len(bookings)}

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
