"""Cal.com API v2 client — credentials from Integrations DB or env."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from acquisition.config import get_settings

logger = logging.getLogger(__name__)

CALCOM_API_VERSION = "2024-06-14"
DEFAULT_BASE = "https://api.cal.com/v2"


class CalcomError(Exception):
    def __init__(self, message: str, *, http_status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def resolve_calcom_api_key(db: Session | None = None) -> str:
    """Resolve Cal.com API key: encrypted Integrations record, then environment."""
    from acquisition.services.calcom_service import CalcomService

    return (CalcomService(db=db).resolve_api_key() or "").strip()


class CalcomClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        db: Session | None = None,
    ) -> None:
        settings = get_settings()
        if api_key is not None:
            self.api_key = api_key.strip()
        else:
            self.api_key = resolve_calcom_api_key(db=db)
        self.base_url = (
            base_url
            or getattr(settings, "calcom_api_base_url", None)
            or DEFAULT_BASE
        ).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and self.api_key.startswith("cal_")

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise CalcomError("Cal.com API key is not configured", http_status=503)
        return {
            "Authorization": f"Bearer {self.api_key}",
            "cal-api-version": CALCOM_API_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, json: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.request(method, url, headers=self._headers(), json=json)
        except httpx.HTTPError as exc:
            logger.warning("Cal.com HTTP error: %s", exc.__class__.__name__)
            raise CalcomError("Cal.com request failed", http_status=502) from exc
        if response.status_code >= 400:
            # Never log Authorization header or raw API key.
            logger.warning("Cal.com API %s %s → %s", method, path, response.status_code)
            detail = "Cal.com API error"
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = str(body.get("message") or body.get("error") or detail)
            except Exception:  # noqa: BLE001
                pass
            raise CalcomError(detail, http_status=response.status_code)
        if not response.content:
            return None
        return response.json()

    def get_me(self) -> dict[str, Any]:
        raw = self._request("GET", "/me")
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com /me response")
        # Return only non-secret account fields needed for Admin.
        return {
            "id": data.get("id"),
            "username": data.get("username"),
            "name": data.get("name"),
            "email": data.get("email"),
            "timeZone": data.get("timeZone"),
            "timeFormat": data.get("timeFormat"),
            "organizationId": data.get("organizationId"),
        }

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

    def ensure_timezone(self, time_zone: str) -> dict[str, Any]:
        """Keep the Cal.com host account on the FreedomBot platform timezone."""
        desired = (time_zone or "").strip()
        if not desired:
            raise CalcomError("Timezone is required")
        me = self.get_me()
        current = str(me.get("timeZone") or "").strip()
        if current == desired:
            return me
        raw = self._request("PATCH", "/me", json={"timeZone": desired})
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com update timezone response")
        return self._me_summary(data)

    def ensure_time_format(self, time_format: int = 12) -> dict[str, Any]:
        """Keep the Cal.com host account on 12-hour clock (AM/PM)."""
        desired = 12 if int(time_format) != 24 else 24
        me = self.get_me()
        current = me.get("timeFormat")
        try:
            current_int = int(current) if current is not None else None
        except (TypeError, ValueError):
            current_int = None
        if current_int == desired:
            return me
        raw = self._request("PATCH", "/me", json={"timeFormat": desired})
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com update timeFormat response")
        return self._me_summary(data)

    def ensure_event_booker_defaults(self, event_type_id: int | str) -> dict[str, Any]:
        """Hide guest UI and prefer English (12h-friendly) booker copy on an event type."""
        safe_id = str(event_type_id).strip()
        if not safe_id:
            raise CalcomError("Cal.com event type id is required")
        raw = self._request(
            "PATCH",
            f"/event-types/{safe_id}",
            json={
                # Hides "Add guests" / "Add another" in the booker.
                # CC emails can still be attached via guests= on the embed URL.
                "disableGuests": True,
                "interfaceLanguage": "en",
            },
        )
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com update event-type response")
        return {
            "id": data.get("id"),
            "disableGuests": data.get("disableGuests"),
            "interfaceLanguage": data.get("interfaceLanguage"),
            "slug": data.get("slug"),
            "title": data.get("title"),
        }

    def set_event_disable_guests(
        self,
        event_type_id: int | str,
        *,
        disabled: bool = True,
    ) -> dict[str, Any]:
        """Hide or show the booker \"Add guests\" / \"Add another\" fields."""
        if disabled:
            return self.ensure_event_booker_defaults(event_type_id)
        safe_id = str(event_type_id).strip()
        if not safe_id:
            raise CalcomError("Cal.com event type id is required")
        raw = self._request(
            "PATCH",
            f"/event-types/{safe_id}",
            json={"disableGuests": False},
        )
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com update event-type response")
        return {
            "id": data.get("id"),
            "disableGuests": data.get("disableGuests"),
            "slug": data.get("slug"),
            "title": data.get("title"),
        }

    def list_event_types(self, *, username: str | None = None) -> list[dict[str, Any]]:
        path = "/event-types"
        if username:
            path = f"/event-types?username={username}"
        raw = self._request("GET", path)
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "slug": item.get("slug"),
                    "lengthInMinutes": item.get("lengthInMinutes") or item.get("length"),
                    "bookingUrl": item.get("bookingUrl"),
                    "description": item.get("description"),
                    "hidden": item.get("hidden"),
                }
            )
        return out

    def create_event_type(
        self,
        *,
        title: str,
        slug: str,
        length_in_minutes: int = 30,
        description: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title.strip(),
            "slug": slug.strip(),
            "lengthInMinutes": int(length_in_minutes),
        }
        if description:
            payload["description"] = description.strip()
        raw = self._request("POST", "/event-types", json=payload)
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com create event-type response")
        return {
            "id": data.get("id"),
            "title": data.get("title"),
            "slug": data.get("slug"),
            "lengthInMinutes": data.get("lengthInMinutes") or data.get("length"),
            "bookingUrl": data.get("bookingUrl"),
        }

    def delete_event_type(self, event_type_id: int | str) -> None:
        """DELETE /event-types/{id}."""
        safe_id = str(event_type_id).strip()
        if not safe_id:
            raise CalcomError("Cal.com event type id is required for delete")
        self._request("DELETE", f"/event-types/{safe_id}")

    def list_webhooks(self) -> list[dict[str, Any]]:
        raw = self._request("GET", "/webhooks")
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def create_webhook(
        self,
        *,
        subscriber_url: str,
        triggers: list[str],
        secret: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subscriberUrl": subscriber_url.strip(),
            "triggers": triggers,
            "active": active,
        }
        if secret:
            payload["secret"] = secret
        raw = self._request("POST", "/webhooks", json=payload)
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise CalcomError("Unexpected Cal.com create webhook response")
        return data

    def delete_webhook(self, webhook_id: int | str) -> None:
        self._request("DELETE", f"/webhooks/{webhook_id}")

    def cancel_booking(
        self,
        uid: str,
        *,
        reason: str = "FreedomBot journey reset",
    ) -> None:
        """Cancel a Cal.com booking by uid (best-effort across API shapes)."""
        cleaned = (uid or "").strip()
        if not cleaned:
            raise CalcomError("Booking uid is required", http_status=422)
        payload = {"cancellationReason": reason}
        errors: list[str] = []
        for path in (
            f"/bookings/{cleaned}/cancel",
            f"/bookings/{cleaned}/cancellations",
        ):
            try:
                self._request("POST", path, json=payload)
                return
            except CalcomError as exc:
                errors.append(f"{path}:{exc.http_status}")
                # Already gone / already cancelled is success for rewind.
                if exc.http_status in {404, 400, 409}:
                    detail = (exc.message or "").lower()
                    if (
                        exc.http_status == 404
                        or "cancel" in detail
                        or "already" in detail
                        or "not found" in detail
                    ):
                        return
                continue
        # Last resort: DELETE booking resource.
        try:
            self._request("DELETE", f"/bookings/{cleaned}")
            return
        except CalcomError as exc:
            errors.append(f"DELETE:/bookings/{cleaned}:{exc.http_status}")
            if exc.http_status in {404, 400, 409}:
                return
            raise CalcomError(
                f"Could not cancel Cal.com booking ({', '.join(errors)})",
                http_status=exc.http_status,
            ) from exc

    def list_bookings(self, *, take: int = 25, status: str | None = "upcoming") -> list[dict[str, Any]]:
        """List bookings (API version may return a list or {bookings: [...]})."""
        path = f"/bookings?take={int(take)}"
        if status:
            path = f"{path}&status={status}"
        # Prefer the newer bookings API shape that includes metadata clearly.
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.request(
                    "GET",
                    url,
                    headers={
                        **self._headers(),
                        "cal-api-version": "2024-08-13",
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("Cal.com HTTP error: %s", exc.__class__.__name__)
            raise CalcomError("Cal.com request failed", http_status=502) from exc
        if response.status_code >= 400:
            # Fall back to default client version.
            raw = self._request("GET", path)
        else:
            raw = response.json() if response.content else None
        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            bookings = data.get("bookings")
            if isinstance(bookings, list):
                return [item for item in bookings if isinstance(item, dict)]
        return []
