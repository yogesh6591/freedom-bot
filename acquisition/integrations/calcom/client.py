"""Cal.com through Composio: no API key lives in this app.

A platform user connects Cal.com on the Integrations page (Composio OAuth, like Gmail). Every call here runs
one Composio ``CAL_*`` tool as that user (see ``owner.py``). The method names and return shapes are the ones
the rest of the app already uses, so callers do not change.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from acquisition.integrations.calcom import owner

logger = logging.getLogger(__name__)

TOOLKIT = owner.TOOLKIT

# Booking statuses Cal.com understands. Older callers say "accepted" / "pending".
ALL_STATUSES = ["upcoming", "recurring", "past", "cancelled", "unconfirmed"]
_STATUS_ALIASES = {"accepted": "upcoming", "pending": "unconfirmed", "canceled": "cancelled"}


class CalcomError(Exception):
    def __init__(self, message: str, *, http_status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _inner(data: Any) -> Any:
    """Cal.com wraps its answer as ``{status, data}``; return the part inside."""
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


def _is_gone(exc: CalcomError) -> bool:
    text = exc.message.lower()
    return any(word in text for word in ("not found", "404", "already", "cancelled", "canceled"))


class CalcomClient:
    def __init__(self, *, db: Session | None = None, ctx: Any | None = None) -> None:
        # ``db`` is accepted for the existing call sites; nothing is read from it any more.
        self._ctx = ctx

    # ------------------------------------------------------------------ plumbing
    def _context(self) -> Any | None:
        return self._ctx or owner.owner_context()

    @property
    def configured(self) -> bool:
        """True when a platform user has Cal.com connected through Composio."""
        return self._ctx is not None or owner.owner_user_id() is not None

    def _run(self, action: str, arguments: dict[str, Any] | None = None) -> Any:
        from bizos.connectors import composio
        from bizos.connectors.base import ConnectorError, ConnectorNotConfigured
        from bizos.tenancy.context import tenant_scope

        ctx = self._context()
        if ctx is None:
            raise CalcomError("Cal.com is not connected. Connect it on the Integrations page.", http_status=503)
        try:
            with tenant_scope(ctx):
                return composio.execute(ctx, TOOLKIT, action, arguments or {})
        except ConnectorNotConfigured as exc:
            raise CalcomError(str(exc), http_status=503) from exc
        except ConnectorError as exc:
            logger.warning("Cal.com %s failed: %s", action, exc)
            raise CalcomError(str(exc), http_status=502) from exc

    # ------------------------------------------------------------------ the host account
    @staticmethod
    def _me_summary(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": data.get("id"),
            "username": data.get("username"),
            "name": data.get("name"),
            "email": data.get("email"),
            "timeZone": data.get("timeZone"),
            "timeFormat": data.get("timeFormat"),
            "organizationId": data.get("organizationId"),
        }

    def get_me(self) -> dict[str, Any]:
        data = _inner(self._run("CAL_RETRIEVE_MY_INFORMATION"))
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com profile response")
        return self._me_summary(data)

    def _update_profile(self, fields: dict[str, Any]) -> dict[str, Any]:
        data = _inner(self._run("CAL_UPDATE_USER_PROFILE_DETAILS", fields))
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com profile update response")
        return self._me_summary(data)

    def ensure_timezone(self, time_zone: str) -> dict[str, Any]:
        """Keep the Cal.com host account on the platform timezone."""
        desired = (time_zone or "").strip()
        if not desired:
            raise CalcomError("Timezone is required")
        me = self.get_me()
        if str(me.get("timeZone") or "").strip() == desired:
            return me
        return self._update_profile({"timeZone": desired})

    def ensure_time_format(self, time_format: int = 12) -> dict[str, Any]:
        """Keep the Cal.com host account on the 12-hour clock (or 24 when asked)."""
        desired = 24 if int(time_format) == 24 else 12
        me = self.get_me()
        try:
            current = int(me.get("timeFormat"))
        except (TypeError, ValueError):
            current = None
        if current == desired:
            return me
        return self._update_profile({"timeFormat": desired})

    # ------------------------------------------------------------------ event types
    def ensure_event_booker_defaults(self, event_type_id: int | str) -> dict[str, Any]:
        """Hide the "Add guests" fields on an event type (the prospect is the only booker)."""
        try:
            event_id = int(str(event_type_id).strip())
        except ValueError:
            raise CalcomError("Cal.com event type id must be a number", http_status=422) from None
        data = _inner(self._run("CAL_EDIT_EVENT_TYPE_BY_ID", {"id": event_id, "disableGuests": True}))
        event = data.get("event_type") if isinstance(data, dict) else None
        if not isinstance(event, dict):
            raise CalcomError("Unexpected Cal.com update event-type response")
        return {
            "id": event.get("id"),
            "disableGuests": event.get("disableGuests"),
            "slug": event.get("slug"),
            "title": event.get("title"),
        }

    def list_event_types(self, *, username: str | None = None) -> list[dict[str, Any]]:
        arguments = {"username": username} if username else {}
        data = _inner(self._run("CAL_LIST_EVENT_TYPES", arguments))
        groups = data.get("eventTypeGroups") if isinstance(data, dict) else None
        if not isinstance(groups, list):
            return []
        groups = [g for g in groups if isinstance(g, dict)]
        # Your own event types first; team event types only when you have none of your own.
        personal = [g for g in groups if not g.get("teamId")]
        out: list[dict[str, Any]] = []
        for group in personal or groups:
            profile = group.get("profile") if isinstance(group.get("profile"), dict) else {}
            base = str(group.get("bookerUrl") or "").rstrip("/")
            owner_slug = str(profile.get("slug") or username or "").strip("/")
            for item in group.get("eventTypes") or []:
                if not isinstance(item, dict):
                    continue
                slug = item.get("slug")
                booking_url = f"{base}/{owner_slug}/{slug}" if base and owner_slug and slug else None
                out.append(
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "slug": slug,
                        "lengthInMinutes": item.get("length"),
                        "bookingUrl": booking_url,
                        "description": item.get("description"),
                        "hidden": item.get("hidden"),
                    }
                )
        return out

    # ------------------------------------------------------------------ webhooks
    def list_webhooks(self) -> list[dict[str, Any]]:
        data = _inner(self._run("CAL_RETRIEVE_WEBHOOKS_LIST", {"take": 100}))
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def create_webhook(
        self,
        *,
        subscriber_url: str,
        triggers: list[str],
        secret: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "subscriberUrl": subscriber_url.strip(),
            "triggers": triggers,
            "active": active,
        }
        if secret:
            arguments["secret"] = secret
        data = _inner(self._run("CAL_POST_WEBHOOK_EVENT", arguments))
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com create webhook response")
        return data

    def delete_webhook(self, webhook_id: int | str) -> None:
        self._run("CAL_DELETE_WEBHOOK_BY_ID", {"webhookId": str(webhook_id)})

    # ------------------------------------------------------------------ bookings
    @staticmethod
    def _booking_summary(raw: Any) -> dict[str, Any]:
        """The few booking fields we store, from a reschedule or get response."""
        data = _inner(raw)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict) or not data.get("uid"):
            raise CalcomError("Unexpected Cal.com booking response")
        return {
            "uid": str(data.get("uid")),
            "id": str(data["id"]) if data.get("id") is not None else None,
            "status": data.get("status"),
            "start": data.get("start") or data.get("startTime"),
            "end": data.get("end") or data.get("endTime"),
            "meetingUrl": data.get("meetingUrl"),
            "location": data.get("location"),
        }

    def cancel_booking(self, uid: str, *, reason: str = "FreedomBot journey reset") -> None:
        """Cancel a booking. One that is already gone or already cancelled counts as done."""
        cleaned = (uid or "").strip()
        if not cleaned:
            raise CalcomError("Booking uid is required", http_status=422)
        try:
            self._run("CAL_CANCEL_BOOKING_VIA_UID", {"bookingUid": cleaned, "cancellationReason": reason})
        except CalcomError as exc:
            if _is_gone(exc):
                return
            raise

    def reschedule_booking(self, uid: str, *, start_utc: str, reason: str | None = None) -> dict[str, Any]:
        """Move a booking. Cal.com returns the new booking (new uid)."""
        cleaned = (uid or "").strip()
        if not cleaned:
            raise CalcomError("Booking uid is required", http_status=422)
        arguments: dict[str, Any] = {"bookingUid": cleaned, "start": start_utc}
        if reason:
            arguments["reschedulingReason"] = reason
        return self._booking_summary(self._run("CAL_RESCHEDULE_BOOKING_BY_UID", arguments))

    def get_booking_record(self, uid: str) -> dict[str, Any]:
        cleaned = (uid or "").strip()
        if not cleaned:
            raise CalcomError("Booking uid is required", http_status=422)
        data = _inner(self._run("CAL_RETRIEVE_BOOKING_DETAILS_BY_UID", {"bookingUid": cleaned}))
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict) or not data.get("uid"):
            raise CalcomError("Unexpected Cal.com booking response")
        return data

    def get_booking(self, uid: str) -> dict[str, Any]:
        return self._booking_summary(self.get_booking_record(uid))

    def list_bookings(self, *, take: int = 25, status: str | None = "upcoming") -> list[dict[str, Any]]:
        """Bookings. ``status=None`` means every status."""
        if status:
            statuses = [_STATUS_ALIASES.get(status.lower(), status.lower())]
        else:
            statuses = list(ALL_STATUSES)
        data = _inner(self._run("CAL_FETCH_ALL_BOOKINGS", {"take": int(take), "status": statuses}))
        if isinstance(data, dict):
            data = data.get("bookings") or data.get("data") or []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
