"""
Composio Connectors
===================

Live Gmail and Google Calendar through Composio managed OAuth, ported from the
ai-automation-saas integration. Connections are **per user**: each person signs
in to their own Google account from the Integrations page, and only their own
chat turns read it. The Composio user id is namespaced by client and user, so a
connection can never be resolved from another workspace.

Reads and writes both go to Google. Writes still pass through the policy engine
first, so in approval mode nothing is sent or scheduled until an approver
releases it. An approved action executes under the approver's login, so the
registry passes the *requester* as ``owner_user_id`` and the mail goes out from
the requester's account, not the approver's.

Everything returned from Google is external content and is neutralized via
:mod:`bizos.connectors.untrusted` before the model sees it (§20).
"""

from __future__ import annotations

import dataclasses
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from agno.utils.log import log_warning

from bizos.connectors.base import Connector, ConnectorError, ConnectorNotConfigured, ConnectorResult
from bizos.connectors.calendar import WorkspaceCalendarConnector
from bizos.connectors.email import WorkspaceEmailConnector
from bizos.connectors.untrusted import wrap_many
from bizos.tenancy.context import TenantContext

#: Composio toolkit slug -> integration category and display metadata.
TOOLKITS: dict[str, dict[str, str]] = {
    "gmail": {
        "category": "email",
        "name": "Gmail",
        "description": "Search, read and send email from chat.",
    },
    "googlecalendar": {
        "category": "calendar",
        "name": "Google Calendar",
        "description": "Ask about meetings, find free time and schedule events.",
    },
    "youtube": {
        "category": "youtube",
        "name": "YouTube",
        "description": "Search YouTube and check your channel, videos and comments.",
    },
    "hubspot": {
        "category": "crm",
        "name": "HubSpot",
        "description": "Use your HubSpot CRM: contacts, companies, deals, notes and tasks.",
    },
}

_STATUS_TTL = 30.0
_BODY_LIMIT = 4000
_SNIPPET_LIMIT = 300

_client: Any = None
_client_lock = threading.Lock()
_status_cache: dict[str, tuple[float, dict[str, Optional[str]]]] = {}
_timezone_cache: dict[str, str] = {}


def _api_key() -> str:
    key = os.environ.get("COMPOSIO_API_KEY", "").strip()
    if not key or key.startswith("ak_your_") or key.startswith("your_"):
        return ""
    return key


def configured() -> bool:
    return bool(_api_key())


def _ensure_cache_dir() -> None:
    """The SDK refuses to import when its cache dir is not writable."""
    if os.environ.get("COMPOSIO_CACHE_DIR"):
        return
    home = os.path.expanduser("~")
    if not os.access(home, os.W_OK):
        os.environ["COMPOSIO_CACHE_DIR"] = os.path.join(tempfile.gettempdir(), "composio")


def get_client() -> Any:
    global _client
    key = _api_key()
    if not key:
        raise ConnectorNotConfigured(
            "Composio is not configured on this server. Set COMPOSIO_API_KEY and restart."
        )
    with _client_lock:
        if _client is None:
            _ensure_cache_dir()
            from composio import Composio  # imported lazily: optional dependency

            _client = Composio(api_key=key)
    return _client


def composio_user_id(ctx: TenantContext) -> str:
    return f"fb:{ctx.client_id}:{ctx.user_id}"


def as_user(ctx: TenantContext, user_id: Optional[str]) -> TenantContext:
    """``ctx`` re-pointed at another user of the same client, for Composio lookups only."""
    if not user_id or user_id == ctx.user_id:
        return ctx
    return dataclasses.replace(ctx, user_id=user_id)


def _check_toolkit(toolkit: str) -> str:
    slug = (toolkit or "").strip().lower()
    if slug not in TOOLKITS:
        raise ValueError(f"Unsupported toolkit {toolkit!r}")
    return slug


def invalidate(ctx: TenantContext) -> None:
    _status_cache.pop(composio_user_id(ctx), None)


def _active_accounts(ctx: TenantContext) -> dict[str, Optional[str]]:
    """toolkit slug -> ACTIVE connected account id (None when not connected)."""
    uid = composio_user_id(ctx)
    cached = _status_cache.get(uid)
    if cached and time.monotonic() - cached[0] < _STATUS_TTL:
        return cached[1]
    found: dict[str, Optional[str]] = {slug: None for slug in TOOLKITS}
    if configured():
        try:
            listing = get_client().connected_accounts.list(
                user_ids=[uid], toolkit_slugs=list(TOOLKITS), statuses=["ACTIVE"]
            )
            for account in getattr(listing, "items", None) or []:
                toolkit = getattr(getattr(account, "toolkit", None), "slug", "") or ""
                slug = toolkit.lower()
                if slug in found and found[slug] is None:
                    found[slug] = account.id
        except Exception as exc:
            log_warning(f"Composio status lookup failed for {uid}: {exc}")
            return found
    _status_cache[uid] = (time.monotonic(), found)
    return found


def connected_toolkit(ctx: TenantContext, category: str) -> Optional[str]:
    """The toolkit serving ``category`` for this user, if they connected one."""
    if not configured() or not ctx.user_id:
        return None
    accounts = _active_accounts(ctx)
    for slug, meta in TOOLKITS.items():
        if meta["category"] == category and accounts.get(slug):
            return slug
    return None


def connected_categories(ctx: TenantContext) -> frozenset[str]:
    if not configured() or not ctx.user_id:
        return frozenset()
    accounts = _active_accounts(ctx)
    return frozenset(TOOLKITS[slug]["category"] for slug, acc in accounts.items() if acc)


def status(ctx: TenantContext) -> dict[str, Any]:
    accounts = _active_accounts(ctx) if configured() else {slug: None for slug in TOOLKITS}
    return {
        "configured": configured(),
        "items": [
            {
                "toolkit": slug,
                "category": meta["category"],
                "name": meta["name"],
                "description": meta["description"],
                "connected": bool(accounts.get(slug)),
            }
            for slug, meta in TOOLKITS.items()
        ],
    }


def connect_url(ctx: TenantContext, toolkit: str, callback_url: Optional[str] = None) -> str:
    slug = _check_toolkit(toolkit)
    client = get_client()
    auth_config_id = client.toolkits._get_auth_config_id(toolkit=slug)
    request = client.connected_accounts.link(
        user_id=composio_user_id(ctx),
        auth_config_id=auth_config_id,
        callback_url=callback_url or None,
        allow_multiple=True,
    )
    invalidate(ctx)
    return str(request.redirect_url)


def disconnect(ctx: TenantContext, toolkit: str) -> None:
    slug = _check_toolkit(toolkit)
    client = get_client()
    listing = client.connected_accounts.list(
        user_ids=[composio_user_id(ctx)], toolkit_slugs=[slug]
    )
    for account in getattr(listing, "items", None) or []:
        client.connected_accounts.delete(account.id)
    invalidate(ctx)


def execute(ctx: TenantContext, toolkit: str, action: str, arguments: dict[str, Any]) -> Any:
    """Run one Composio tool against this user's connected account."""
    account_id = _active_accounts(ctx).get(toolkit)
    if not account_id:
        raise ConnectorNotConfigured(
            f"{TOOLKITS[toolkit]['name']} is not connected for your account. "
            "Connect it on the Integrations page."
        )
    try:
        raw = get_client().tools.execute(
            action,
            arguments,
            connected_account_id=account_id,
            user_id=composio_user_id(ctx),
            dangerously_skip_version_check=True,
        )
    except Exception as exc:
        raise ConnectorError(f"{TOOLKITS[toolkit]['name']} request failed: {exc}") from exc
    payload = raw if isinstance(raw, dict) else {
        "data": getattr(raw, "data", None),
        "error": getattr(raw, "error", None),
        "successful": getattr(raw, "successful", True),
    }
    if payload.get("error") or payload.get("successful") is False:
        raise ConnectorError(
            f"{TOOLKITS[toolkit]['name']} request failed: {payload.get('error') or 'unknown error'}"
        )
    return payload.get("data")


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _gmail_messages(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("messages", "items"):
            if isinstance(data.get(key), list):
                return [m for m in data[key] if isinstance(m, dict)]
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    return []


def _gmail_preview(msg: dict[str, Any]) -> str:
    preview = msg.get("preview")
    if isinstance(preview, dict):
        preview = preview.get("body")
    return str(msg.get("snippet") or preview or "")


def _gmail_thread_id(value: Any) -> Optional[str]:
    """Gmail ids are hex; workspace ids (``thr_…``) must not be sent to Google."""
    text = str(value or "").strip()
    return text if re.fullmatch(r"[0-9a-fA-F]{8,}", text) else None


def _recipient_args(recipients: list[str], cc: Optional[list[str]]) -> dict[str, Any]:
    args: dict[str, Any] = {"recipient_email": recipients[0]}
    if len(recipients) > 1:
        args["extra_recipients"] = recipients[1:]
    if cc:
        args["cc"] = list(cc)
    return args


def _sent_ids(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    inner = data.get("response_data") if isinstance(data.get("response_data"), dict) else data
    message = inner.get("message") if isinstance(inner.get("message"), dict) else inner
    return {
        "gmail_id": inner.get("id") or message.get("id"),
        "gmail_thread_id": message.get("threadId") or inner.get("threadId"),
    }


def _gmail_row(msg: dict[str, Any], *, body_limit: int) -> dict[str, Any]:
    body = msg.get("messageText") or _gmail_preview(msg)
    return {
        "message_id": msg.get("messageId") or msg.get("id"),
        "thread_id": msg.get("threadId") or msg.get("thread_id"),
        "subject": msg.get("subject") or "",
        "sender": msg.get("sender") or msg.get("from") or "",
        "to": msg.get("to") or "",
        "sent_at": msg.get("messageTimestamp") or msg.get("internalDate") or msg.get("date"),
        "labels": msg.get("labelIds") or [],
        "body": _clip(body, body_limit),
    }


class _ComposioIdentity:
    """Resolves whose Google account a connector call runs against."""

    ctx: TenantContext
    config: dict[str, Any]

    @property
    def owner(self) -> TenantContext:
        return as_user(self.ctx, self.config.get("owner_user_id"))


class ComposioGmailConnector(_ComposioIdentity, WorkspaceEmailConnector):
    """The user's own Gmail: search, read, draft and send."""

    provider = "composio_gmail"
    toolkit = "gmail"

    def health(self) -> ConnectorResult:
        try:
            execute(self.owner, self.toolkit, "GMAIL_FETCH_EMAILS", {"max_results": 1, "ids_only": True})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, summary="gmail connected")

    def search_threads(self, query: str = "", *, limit: int = 20) -> ConnectorResult:
        args: dict[str, Any] = {
            "user_id": "me",
            "max_results": max(1, min(int(limit or 20), 25)),
            "include_payload": False,
            "verbose": False,
        }
        if query:
            args["query"] = query
        try:
            data = execute(self.owner, self.toolkit, "GMAIL_FETCH_EMAILS", args)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        rows = [_gmail_row(m, body_limit=_SNIPPET_LIMIT) for m in _gmail_messages(data)]
        rows, findings = wrap_many(rows, source="gmail", text_field="body", id_field="message_id")
        return ConnectorResult(
            ok=True,
            data=rows,
            summary=f"{len(rows)} Gmail message(s)" + (f" for {query!r}" if query else ""),
            untrusted_findings=findings,
            metadata={"provider": self.provider},
        )

    def read_thread(self, thread_id: str) -> ConnectorResult:
        if not thread_id:
            return ConnectorResult.failure("thread_id is required")
        try:
            data = execute(
                self.owner,
                self.toolkit,
                "GMAIL_FETCH_MESSAGE_BY_THREAD_ID",
                {"user_id": "me", "thread_id": thread_id},
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        rows = [_gmail_row(m, body_limit=_BODY_LIMIT) for m in _gmail_messages(data)]
        if not rows:
            return ConnectorResult.failure(f"No Gmail thread {thread_id!r}")
        rows, findings = wrap_many(rows, source="gmail", text_field="body", id_field="message_id")
        return ConnectorResult(
            ok=True,
            data={"thread_id": thread_id, "messages": rows},
            summary=f"{len(rows)} message(s) in Gmail thread {thread_id}",
            untrusted_findings=findings,
            metadata={"provider": self.provider},
        )

    def create_draft(
        self,
        *,
        recipients: list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        action_id: Optional[str] = None,
    ) -> ConnectorResult:
        if not recipients:
            return ConnectorResult.failure("At least one recipient is required")
        args: dict[str, Any] = {
            "user_id": "me",
            "subject": subject,
            "body": body,
            **_recipient_args(recipients, cc),
        }
        gmail_thread = _gmail_thread_id(thread_id)
        if gmail_thread:
            args["thread_id"] = gmail_thread
        try:
            data = execute(self.owner, self.toolkit, "GMAIL_CREATE_EMAIL_DRAFT", args)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        ids = _sent_ids(data)
        return ConnectorResult(
            ok=True,
            data={"draft_id": ids.get("gmail_id"), "recipients": recipients, "subject": subject, **ids},
            summary=f"Gmail draft saved for {', '.join(recipients)} — not sent",
            metadata={"provider": self.provider},
        )

    def send_message(
        self,
        *,
        recipients: list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        draft_id: Optional[str] = None,
    ) -> ConnectorResult:
        if not recipients:
            return ConnectorResult.failure("At least one recipient is required")
        gmail_thread = _gmail_thread_id(thread_id)
        if gmail_thread:
            action = "GMAIL_REPLY_TO_THREAD"
            args: dict[str, Any] = {
                "user_id": "me",
                "thread_id": gmail_thread,
                "message_body": body,
                **_recipient_args(recipients, cc),
            }
        else:
            action = "GMAIL_SEND_EMAIL"
            args = {"user_id": "me", "subject": subject, "body": body, **_recipient_args(recipients, cc)}
        try:
            data = execute(self.owner, self.toolkit, action, args)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        ids = _sent_ids(data)
        return ConnectorResult(
            ok=True,
            data={"message_id": ids.get("gmail_id"), "thread_id": ids.get("gmail_thread_id"), "recipients": recipients},
            summary=f"email sent from Gmail to {', '.join(recipients)}",
            metadata={"provider": self.provider},
        )


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------


def _zone(name: Optional[str]) -> Any:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except Exception:
        return timezone.utc


def _moment(value: Optional[str], default: datetime, tz: Any = timezone.utc) -> datetime:
    """Parse an ISO time; a naive time is wall-clock time in ``tz``."""
    if not value:
        moment = default
    else:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment


def _rfc3339(value: Optional[str], default: datetime, tz: Any = timezone.utc) -> str:
    try:
        return _moment(value, default, tz).isoformat()
    except ValueError:
        return str(value)


def _event_link(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    event = data.get("response_data") if isinstance(data.get("response_data"), dict) else data
    return {
        "event_id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "meeting_link": event.get("hangoutLink"),
    }


def _calendar_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("items", "events", "value"):
            if isinstance(data.get(key), list):
                return [e for e in data[key] if isinstance(e, dict)]
        nested = data.get("response_data") or data.get("data")
        if nested is not None and nested is not data:
            return _calendar_items(nested)
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def _when(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("dateTime") or value.get("date")
    return value


def _event_row(event: dict[str, Any]) -> dict[str, Any]:
    attendees = [
        a.get("email") for a in event.get("attendees") or [] if isinstance(a, dict) and a.get("email")
    ]
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(no title)",
        "starts_at": _when(event.get("start")),
        "ends_at": _when(event.get("end")),
        "all_day": isinstance(event.get("start"), dict) and "date" in event["start"],
        "location": event.get("location"),
        "organizer": organizer.get("email"),
        "attendees": attendees,
        "status": event.get("status"),
        "meeting_link": event.get("hangoutLink"),
        "description": _clip(event.get("description"), 1000),
    }


class ComposioCalendarConnector(_ComposioIdentity, WorkspaceCalendarConnector):
    """The user's own Google Calendar: list, free/busy, create, move and cancel events."""

    provider = "composio_googlecalendar"
    toolkit = "googlecalendar"

    def _timezone(self) -> str:
        """The calendar's own time zone; naive times from the model are read in it."""
        key = composio_user_id(self.owner)
        if key in _timezone_cache:
            return _timezone_cache[key]
        name = "UTC"
        try:
            data = execute(self.owner, self.toolkit, "GOOGLECALENDAR_GET_CALENDAR", {"calendar_id": "primary"})
            if isinstance(data, dict):
                inner = data.get("response_data") if isinstance(data.get("response_data"), dict) else data
                calendar = inner.get("calendar") if isinstance(inner.get("calendar"), dict) else inner
                name = str(calendar.get("timeZone") or calendar.get("time_zone") or "UTC")
        except ConnectorError as exc:
            log_warning(f"Could not read Google Calendar time zone: {exc}")
            return name
        _timezone_cache[key] = name
        return name

    def health(self) -> ConnectorResult:
        now = datetime.now(timezone.utc)
        try:
            execute(
                self.owner,
                self.toolkit,
                "GOOGLECALENDAR_EVENTS_LIST",
                {"calendarId": "primary", "maxResults": 1, "timeMin": now.isoformat()},
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, summary="google calendar connected")

    def list_events(
        self, *, start: Optional[str] = None, end: Optional[str] = None, limit: int = 50
    ) -> ConnectorResult:
        tz = _zone(self._timezone())
        now = datetime.now(timezone.utc)
        time_min = _rfc3339(start, now, tz)
        time_max = _rfc3339(end, now + timedelta(days=7), tz)
        try:
            data = execute(
                self.owner,
                self.toolkit,
                "GOOGLECALENDAR_EVENTS_LIST",
                {
                    "calendarId": "primary",
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "maxResults": max(1, min(int(limit or 50), 100)),
                },
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        events = [_event_row(e) for e in _calendar_items(data) if e.get("status") != "cancelled"]
        events, findings = wrap_many(events, source="google_calendar", text_field="description")
        return ConnectorResult(
            ok=True,
            data=events,
            summary=f"{len(events)} Google Calendar event(s) between {time_min} and {time_max}",
            untrusted_findings=findings,
            metadata={"provider": self.provider},
        )

    def check_availability(self, *, start: str, end: str, slot_minutes: int = 30) -> ConnectorResult:
        listed = self.list_events(start=start, end=end, limit=100)
        if not listed.ok:
            return listed
        tz = _zone(self._timezone())
        try:
            window_start = _moment(start, datetime.now(timezone.utc), tz)
            window_end = _moment(end, window_start + timedelta(days=1), tz)
        except ValueError:
            return ConnectorResult.failure("start and end must be ISO datetimes")
        busy: list[tuple[datetime, datetime]] = []
        for event in listed.data:
            if event.get("all_day") or not event.get("starts_at") or not event.get("ends_at"):
                continue
            try:
                busy.append(
                    (
                        datetime.fromisoformat(_rfc3339(event["starts_at"], window_start)),
                        datetime.fromisoformat(_rfc3339(event["ends_at"], window_start)),
                    )
                )
            except ValueError:
                continue
        busy.sort()
        free: list[dict[str, str]] = []
        cursor = window_start
        for busy_start, busy_end in busy:
            if busy_start > cursor and (busy_start - cursor).total_seconds() / 60 >= slot_minutes:
                free.append({"start": cursor.isoformat(), "end": busy_start.isoformat()})
            cursor = max(cursor, busy_end)
        if window_end > cursor and (window_end - cursor).total_seconds() / 60 >= slot_minutes:
            free.append({"start": cursor.isoformat(), "end": window_end.isoformat()})
        return ConnectorResult(
            ok=True,
            data={"free": free, "busy_count": len(busy)},
            summary=f"{len(free)} free window(s), {len(busy)} busy block(s) on Google Calendar",
            metadata={"provider": self.provider},
        )

    def create_event(
        self,
        *,
        title: str,
        starts_at: str,
        ends_at: str,
        attendees: Optional[list[str]] = None,
        description: str = "",
        location: Optional[str] = None,
        calendar_id: str = "primary",
    ) -> ConnectorResult:
        tz_name = self._timezone()
        tz = _zone(tz_name)
        try:
            start = _moment(starts_at, datetime.now(timezone.utc), tz).astimezone(tz)
            end = (
                _moment(ends_at, start, tz).astimezone(tz)
                if ends_at and ends_at != "None"
                else start + timedelta(minutes=30)
            )
        except ValueError:
            return ConnectorResult.failure("starts_at and ends_at must be ISO datetimes")
        if end <= start:
            end = start + timedelta(minutes=30)
        args: dict[str, Any] = {
            "calendar_id": calendar_id or "primary",
            "summary": title,
            "start_datetime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end_datetime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timezone": tz_name,
            "send_updates": "all",
        }
        if attendees:
            args["attendees"] = list(attendees)
        if description:
            args["description"] = description
        if location:
            args["location"] = location
        try:
            data = execute(self.owner, self.toolkit, "GOOGLECALENDAR_CREATE_EVENT", args)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(
            ok=True,
            data={"title": title, "starts_at": start.isoformat(), "ends_at": end.isoformat(), **_event_link(data)},
            summary=f"Google Calendar event '{title}' created for {start.strftime('%a %d %b %H:%M')} ({tz_name})",
            metadata={"provider": self.provider},
        )

    def reschedule_event(self, event_id: str, *, starts_at: str, ends_at: str) -> ConnectorResult:
        tz = _zone(self._timezone())
        try:
            start = _moment(starts_at, datetime.now(timezone.utc), tz)
            end = _moment(ends_at, start + timedelta(minutes=30), tz) if ends_at and ends_at != "None" else start + timedelta(minutes=30)
        except ValueError:
            return ConnectorResult.failure("starts_at and ends_at must be ISO datetimes")
        try:
            data = execute(
                self.owner,
                self.toolkit,
                "GOOGLECALENDAR_PATCH_EVENT",
                {
                    "calendar_id": "primary",
                    "event_id": event_id,
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "send_updates": "all",
                },
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(
            ok=True,
            data={"event_id": event_id, "starts_at": start.isoformat(), **_event_link(data)},
            summary=f"Google Calendar event moved to {start.strftime('%a %d %b %H:%M')}",
            metadata={"provider": self.provider},
        )

    def cancel_event(self, event_id: str) -> ConnectorResult:
        try:
            execute(
                self.owner,
                self.toolkit,
                "GOOGLECALENDAR_DELETE_EVENT",
                {"calendar_id": "primary", "event_id": event_id, "send_updates": "all"},
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(
            ok=True,
            data={"event_id": event_id},
            summary="Google Calendar event cancelled",
            metadata={"provider": self.provider},
        )


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


def _yt_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return [i for i in data["items"] if isinstance(i, dict)]
        nested = data.get("response_data") or data.get("data")
        if nested is not None and nested is not data:
            return _yt_items(nested)
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def _yt_video_id(item: dict[str, Any]) -> Optional[str]:
    raw = item.get("id")
    if isinstance(raw, dict):
        return raw.get("videoId")
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    resource = snippet.get("resourceId") if isinstance(snippet.get("resourceId"), dict) else {}
    content = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    return resource.get("videoId") or content.get("videoId") or (raw if isinstance(raw, str) else None)


def _count(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _yt_video_row(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    video_id = _yt_video_id(item)
    row: dict[str, Any] = {
        "video_id": video_id,
        "title": snippet.get("title") or "",
        "channel": snippet.get("channelTitle"),
        "published_at": snippet.get("publishedAt"),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        "description": _clip(snippet.get("description"), 500),
    }
    if stats:
        row.update(
            {
                "views": _count(stats.get("viewCount")),
                "likes": _count(stats.get("likeCount")),
                "comments": _count(stats.get("commentCount")),
            }
        )
    return row


class ComposioYouTubeConnector(_ComposioIdentity, Connector):
    """The user's own YouTube account: search, channel stats, videos and comments. Read-only."""

    category = "youtube"
    provider = "composio_youtube"
    toolkit = "youtube"

    def _run(self, action: str, args: dict[str, Any]) -> Any:
        return execute(self.owner, self.toolkit, action, args)

    def health(self) -> ConnectorResult:
        try:
            self._run("YOUTUBE_GET_CHANNEL_STATISTICS", {"mine": True, "part": "snippet"})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, summary="youtube connected")

    def _details(self, video_ids: list[str]) -> list[dict[str, Any]]:
        ids = [v for v in video_ids if v][:50]
        if not ids:
            return []
        data = self._run("YOUTUBE_GET_VIDEO_DETAILS_BATCH", {"id": ids, "parts": ["snippet", "statistics"]})
        return [_yt_video_row(i) for i in _yt_items(data)]

    def _videos_result(self, rows: list[dict[str, Any]], summary: str) -> ConnectorResult:
        rows, findings = wrap_many(rows, source="youtube", text_field="description", id_field="video_id")
        return ConnectorResult(
            ok=True, data=rows, summary=summary, untrusted_findings=findings, metadata={"provider": self.provider}
        )

    def search_videos(self, query: str, *, limit: int = 5, order: Optional[str] = None) -> ConnectorResult:
        if not query:
            return ConnectorResult.failure("query is required")
        args: dict[str, Any] = {
            "q": query,
            "part": "snippet",
            "type": "video",
            "maxResults": max(1, min(int(limit or 5), 25)),
        }
        if order in {"relevance", "date", "viewCount", "rating"}:
            args["order"] = order
        try:
            rows = [_yt_video_row(i) for i in _yt_items(self._run("YOUTUBE_SEARCH_YOU_TUBE", args))]
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return self._videos_result(rows, f"{len(rows)} YouTube result(s) for {query!r}")

    def my_channel(self) -> ConnectorResult:
        try:
            items = _yt_items(
                self._run("YOUTUBE_GET_CHANNEL_STATISTICS", {"mine": True, "part": "snippet,statistics"})
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        if not items:
            return ConnectorResult.failure("This Google account has no YouTube channel")
        item = items[0]
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        channel = {
            "channel_id": item.get("id"),
            "title": snippet.get("title"),
            "handle": snippet.get("customUrl"),
            "created_at": snippet.get("publishedAt"),
            "subscribers": None if stats.get("hiddenSubscriberCount") else _count(stats.get("subscriberCount")),
            "views": _count(stats.get("viewCount")),
            "videos": _count(stats.get("videoCount")),
            "description": _clip(snippet.get("description"), 500),
        }
        [channel], findings = wrap_many([channel], source="youtube", text_field="description", id_field="channel_id")
        return ConnectorResult(
            ok=True,
            data=channel,
            summary=f"YouTube channel {channel['title']!r}",
            untrusted_findings=findings,
            metadata={"provider": self.provider},
        )

    def my_videos(self, *, limit: int = 10) -> ConnectorResult:
        try:
            listed = _yt_items(
                self._run(
                    "YOUTUBE_LIST_CHANNEL_VIDEOS",
                    {"mine": True, "part": "snippet", "maxResults": max(1, min(int(limit or 10), 50))},
                )
            )
            ids = [_yt_video_id(i) for i in listed]
            rows = self._details([i for i in ids if i]) or [_yt_video_row(i) for i in listed]
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return self._videos_result(rows, f"{len(rows)} video(s) on your channel")

    def video_details(self, video_ids: list[str]) -> ConnectorResult:
        if not video_ids:
            return ConnectorResult.failure("video_ids is required")
        try:
            rows = self._details(video_ids)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return self._videos_result(rows, f"details for {len(rows)} video(s)")

    def video_comments(self, video_id: str, *, limit: int = 20, order: str = "relevance") -> ConnectorResult:
        if not video_id:
            return ConnectorResult.failure("video_id is required")
        try:
            data = self._run(
                "YOUTUBE_LIST_COMMENT_THREADS2",
                {
                    "videoId": video_id,
                    "part": "snippet",
                    "maxResults": max(1, min(int(limit or 20), 100)),
                    "order": order if order in {"time", "relevance"} else "relevance",
                    "textFormat": "plainText",
                },
            )
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        rows = []
        for thread in _yt_items(data):
            snippet = thread.get("snippet") if isinstance(thread.get("snippet"), dict) else {}
            top = snippet.get("topLevelComment") if isinstance(snippet.get("topLevelComment"), dict) else {}
            comment = top.get("snippet") if isinstance(top.get("snippet"), dict) else {}
            rows.append(
                {
                    "comment_id": thread.get("id"),
                    "author": comment.get("authorDisplayName"),
                    "text": _clip(comment.get("textDisplay") or comment.get("textOriginal"), 800),
                    "likes": _count(comment.get("likeCount")),
                    "replies": _count(snippet.get("totalReplyCount")),
                    "published_at": comment.get("publishedAt"),
                }
            )
        rows, findings = wrap_many(rows, source="youtube_comment", text_field="text", id_field="comment_id")
        return ConnectorResult(
            ok=True,
            data=rows,
            summary=f"{len(rows)} comment(s) on video {video_id}",
            untrusted_findings=findings,
            metadata={"provider": self.provider},
        )


# ---------------------------------------------------------------------------
# HubSpot (CRM)
# ---------------------------------------------------------------------------

#: Our contact fields -> HubSpot contact properties.
_HS_CONTACT_FIELDS = {
    "first_name": "firstname", "last_name": "lastname", "email": "email", "phone": "phone",
    "company": "company", "title": "jobtitle", "owner": "hubspot_owner_id", "lifecycle": "lifecyclestage",
}
_HS_CONTACT_PROPS = list(_HS_CONTACT_FIELDS.values()) + ["hs_lead_status", "lastmodifieddate"]
_HS_DEAL_PROPS = ["dealname", "dealstage", "amount", "closedate", "pipeline", "hubspot_owner_id"]
_HS_COMPANY_PROPS = ["name", "domain", "industry", "city", "country"]
#: Common stage words -> HubSpot's default sales pipeline stage ids. Anything else is passed through as given
#: (a custom pipeline's own stage id).
_HS_STAGES = {
    "new": "appointmentscheduled", "appointment": "appointmentscheduled", "qualified": "qualifiedtobuy",
    "proposal": "presentationscheduled", "presentation": "presentationscheduled",
    "decision": "decisionmakerboughtin", "negotiation": "contractsent", "contract": "contractsent",
    "won": "closedwon", "closed won": "closedwon", "lost": "closedlost", "closed lost": "closedlost",
    # HubSpot's own stage labels, as users read them on the deal board.
    "appointment scheduled": "appointmentscheduled", "qualified to buy": "qualifiedtobuy",
    "presentation scheduled": "presentationscheduled", "decision maker bought-in": "decisionmakerboughtin",
    "decision maker bought in": "decisionmakerboughtin", "contract sent": "contractsent",
}
#: HubSpot returns at most 100 records per search; counts page on until this many have been read.
_HS_PAGE = 100
_HS_MAX_ROWS = 2000
#: HubSpot association type ids (HUBSPOT_DEFINED) for a note or task linked to a contact or deal.
_HS_ASSOC = {("note", "contact"): 202, ("note", "deal"): 214, ("task", "contact"): 204, ("task", "deal"): 216}


def _hs_unwrap(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("response_data"), (dict, list)):
        return data["response_data"]
    return data


def _hs_results(data: Any) -> list[dict[str, Any]]:
    data = _hs_unwrap(data)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "contacts", "deals", "companies", "items", "data"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def _hs_next_after(data: Any) -> Optional[str]:
    """HubSpot's cursor for the next page of a search (``paging.next.after``), if there is one."""
    paging = data.get("paging") if isinstance(data, dict) else None
    nxt = paging.get("next") if isinstance(paging, dict) else None
    after = nxt.get("after") if isinstance(nxt, dict) else None
    return str(after) if after not in (None, "") else None


def _hs_props(record: dict[str, Any]) -> dict[str, Any]:
    props = record.get("properties")
    return props if isinstance(props, dict) else record


def _hs_contact(record: dict[str, Any]) -> dict[str, Any]:
    p = _hs_props(record)
    return {
        "id": str(record.get("id") or p.get("hs_object_id") or ""),
        "first_name": p.get("firstname") or "", "last_name": p.get("lastname") or "",
        "email": p.get("email"), "phone": p.get("phone"), "company": p.get("company"),
        "title": p.get("jobtitle"), "lifecycle": p.get("lifecyclestage"), "lead_status": p.get("hs_lead_status"),
        "owner": p.get("hubspot_owner_id"), "source": "hubspot",
    }


def _hs_deal(record: dict[str, Any]) -> dict[str, Any]:
    p = _hs_props(record)
    try:
        amount = float(p.get("amount")) if p.get("amount") not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    return {"id": str(record.get("id") or p.get("hs_object_id") or ""), "name": p.get("dealname"),
            "stage": p.get("dealstage"), "amount": amount, "close_date": p.get("closedate"),
            "pipeline": p.get("pipeline"), "owner": p.get("hubspot_owner_id"), "source": "hubspot"}


def _hs_company(record: dict[str, Any]) -> dict[str, Any]:
    p = _hs_props(record)
    return {"id": str(record.get("id") or p.get("hs_object_id") or ""), "name": p.get("name"),
            "domain": p.get("domain"), "industry": p.get("industry"), "city": p.get("city"),
            "country": p.get("country"), "source": "hubspot"}


def _hs_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ComposioHubSpotConnector(_ComposioIdentity, Connector):
    """The user's own HubSpot CRM, with the same methods as the workspace CRM (bizos/connectors/mock_crm.py)."""

    category = "crm"
    provider = "composio_hubspot"
    toolkit = "hubspot"

    def _run(self, action: str, arguments: dict[str, Any]) -> Any:
        return execute(self.owner, self.toolkit, action, arguments)

    def _search(self, action: str, *, query: str = "", filters: Optional[list[dict]] = None,
                properties: list[str], limit: int) -> tuple[list[dict[str, Any]], int]:
        """Up to ``limit`` records, following HubSpot's ``after`` cursor past its 100-per-page cap.

        Returns the rows and HubSpot's own total, so callers can tell a partial list from a full one.
        A later page that fails ends the paging; the rows read so far are kept.
        """
        wanted = max(1, min(int(limit or 20), _HS_MAX_ROWS))
        args: dict[str, Any] = {"limit": min(wanted, _HS_PAGE), "properties": properties}
        if query:
            args["query"] = query
        if not query and not filters and action == "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA":
            # contact search needs a query or a filter; this one matches every contact
            filters = [{"propertyName": "hs_object_id", "operator": "HAS_PROPERTY"}]
        if filters:
            args["filterGroups"] = [{"filters": filters}]
        rows: list[dict[str, Any]] = []
        total: Optional[int] = None
        while True:
            try:
                data = self._run(action, args)
            except ConnectorError:
                if not rows:
                    raise
                break
            page = _hs_results(data)
            rows += page
            inner = _hs_unwrap(data)
            if total is None and isinstance(inner, dict) and isinstance(inner.get("total"), int):
                total = inner["total"]
            after = _hs_next_after(inner)
            if not page or not after or len(rows) >= wanted:
                break
            args = {**args, "after": after, "limit": min(wanted - len(rows), _HS_PAGE)}
        rows = rows[:wanted]
        return rows, total if total is not None else len(rows)

    def health(self) -> ConnectorResult:
        try:
            self._search("HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA", properties=["email"], limit=1)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, summary="hubspot connected")

    # -- reads --------------------------------------------------------------

    def search_contacts(self, query: str = "", *, lifecycle: Optional[str] = None, limit: int = 20) -> ConnectorResult:
        filters = [{"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle}] if lifecycle else None
        try:
            rows, _ = self._search("HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA", query=query, filters=filters,
                                   properties=_HS_CONTACT_PROPS, limit=limit)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        records = [_hs_contact(r) for r in rows]
        return ConnectorResult(ok=True, data=records, summary=f"{len(records)} HubSpot contact(s) found",
                               metadata={"provider": self.provider})

    def _contact_id(self, contact_id: str) -> Optional[str]:
        """A HubSpot contact id, also accepting an email address."""
        ref = (contact_id or "").strip()
        if "@" not in ref:
            return ref or None
        rows, _ = self._search("HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA",
                               filters=[{"propertyName": "email", "operator": "EQ", "value": ref}],
                               properties=["email"], limit=1)
        return str(rows[0].get("id")) if rows else None

    def get_contact(self, contact_id: str) -> ConnectorResult:
        try:
            cid = self._contact_id(contact_id)
            if not cid:
                return ConnectorResult.failure(f"No HubSpot contact matching {contact_id!r}")
            record = _hs_unwrap(self._run("HUBSPOT_READ_CONTACT", {"contactId": cid, "properties": _HS_CONTACT_PROPS}))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        if not isinstance(record, dict) or not (record.get("id") or record.get("properties")):
            return ConnectorResult.failure(f"No HubSpot contact matching {contact_id!r}")
        contact = _hs_contact(record)
        notes: list[dict[str, Any]] = []
        try:
            for n in _hs_results(self._run("HUBSPOT_LIST_CONTACT_NOTES", {"contact_id": cid, "limit": 20})):
                p = _hs_props(n)
                notes.append({"id": str(n.get("id") or ""), "body": p.get("hs_note_body") or p.get("body") or "",
                              "created_at": p.get("hs_timestamp") or p.get("hs_createdate")})
        except ConnectorError as exc:
            log_warning(f"HubSpot notes for contact {cid} unavailable: {exc}")
        safe_notes, findings = wrap_many(notes, source="crm_note", text_field="body")
        contact["notes"] = safe_notes
        name = f"{contact['first_name']} {contact['last_name']}".strip()
        return ConnectorResult(ok=True, data=contact, summary=f"{name} ({contact.get('email') or 'no email'})",
                               untrusted_findings=findings, metadata={"provider": self.provider})

    def search_companies(self, query: str = "", *, limit: int = 20) -> ConnectorResult:
        try:
            rows, _ = self._search("HUBSPOT_SEARCH_COMPANIES", query=query, properties=_HS_COMPANY_PROPS, limit=limit)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        records = [_hs_company(r) for r in rows]
        return ConnectorResult(ok=True, data=records, summary=f"{len(records)} HubSpot company/companies",
                               metadata={"provider": self.provider})

    def search_deals(self, *, stage: Optional[str] = None, owner: Optional[str] = None, limit: int = 50) -> ConnectorResult:
        filters = []
        if stage:
            filters.append({"propertyName": "dealstage", "operator": "EQ", "value": _HS_STAGES.get(stage.strip().lower(), stage)})
        if owner and str(owner).isdigit():
            filters.append({"propertyName": "hubspot_owner_id", "operator": "EQ", "value": str(owner)})
        try:
            rows, total = self._search("HUBSPOT_SEARCH_DEALS", filters=filters or None, properties=_HS_DEAL_PROPS,
                                       limit=limit)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        records = [_hs_deal(r) for r in rows]
        summary = f"{len(records)} HubSpot deal(s)"
        if total > len(records):
            summary = f"Showing {len(records)} of {total} HubSpot deal(s); the total count is {total}"
        return ConnectorResult(ok=True, data=records, summary=summary,
                               metadata={"provider": self.provider, "total": total})

    def pipeline_summary(self) -> ConnectorResult:
        listed = self.search_deals(limit=_HS_MAX_ROWS)
        if not listed.ok:
            return listed
        total_deals = int(listed.metadata.get("total") or len(listed.data))
        by_stage: dict[str, dict[str, Any]] = {}
        for deal in listed.data:
            s = by_stage.setdefault(str(deal["stage"]), {"stage": deal["stage"], "deals": 0, "total": 0.0})
            s["deals"] += 1
            s["total"] += deal["amount"] or 0.0
        stages = sorted(by_stage.values(), key=lambda s: s["total"], reverse=True)
        total = sum(s["total"] for s in stages)
        return ConnectorResult(
            ok=True,
            data={"stages": stages, "total_value": total, "deals_counted": len(listed.data),
                  "deals_in_hubspot": total_deals},
            summary=(f"{len(listed.data)} HubSpot deals worth {total:,.2f} across {len(stages)} stages"
                     + (f" (only {len(listed.data)} of {total_deals} could be read; counts are partial)"
                        if total_deals > len(listed.data) else "")),
            metadata={"provider": self.provider},
        )

    def count_contacts(self, *, lifecycle: Optional[str] = None) -> int:
        filters = [{"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle}] if lifecycle else None
        try:
            _, total = self._search("HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA", filters=filters, properties=["email"], limit=1)
        except ConnectorError:
            return 0
        return int(total)

    def find_duplicate(self, email: str) -> ConnectorResult:
        try:
            cid = self._contact_id(email) if email else None
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": cid} if cid else None,
                               summary="duplicate found" if cid else "no duplicate")

    # -- writes -------------------------------------------------------------

    def _associations(self, kind: str, contact_id: Optional[str], deal_id: Optional[str]) -> list[dict[str, Any]]:
        out = []
        for target, ref in (("contact", contact_id), ("deal", deal_id)):
            if ref:
                out.append({"to": {"id": str(ref)},
                            "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                       "associationTypeId": _HS_ASSOC[(kind, target)]}]})
        return out

    def create_note(self, *, body: str, contact_id: Optional[str] = None, deal_id: Optional[str] = None) -> ConnectorResult:
        args: dict[str, Any] = {"hs_note_body": body, "hs_timestamp": _hs_now()}
        assoc = self._associations("note", contact_id, deal_id)
        if assoc:
            args["associations"] = assoc
        try:
            data = _hs_unwrap(self._run("HUBSPOT_CREATE_NOTE", args))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        note_id = str(data.get("id")) if isinstance(data, dict) and data.get("id") else None
        return ConnectorResult(ok=True, data={"id": note_id}, summary=f"HubSpot note {note_id or ''} created".replace("  ", " "),
                               metadata={"provider": self.provider})

    def create_task(self, *, title: str, body: str = "", contact_id: Optional[str] = None, deal_id: Optional[str] = None,
                    assignee: Optional[str] = None, due_at: Optional[str] = None) -> ConnectorResult:
        args: dict[str, Any] = {"hs_task_subject": title, "hs_task_body": body or "", "hs_task_status": "NOT_STARTED",
                                "hs_task_type": "TODO", "hs_timestamp": due_at or _hs_now()}
        if assignee and str(assignee).isdigit():
            args["hubspot_owner_id"] = str(assignee)
        assoc = self._associations("task", contact_id, deal_id)
        if assoc:
            args["associations"] = assoc
        try:
            data = _hs_unwrap(self._run("HUBSPOT_CREATE_TASK", args))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        task_id = str(data.get("id")) if isinstance(data, dict) and data.get("id") else None
        return ConnectorResult(ok=True, data={"id": task_id}, summary=f"HubSpot task '{title}' created",
                               metadata={"provider": self.provider})

    def _update_contact(self, contact_id: str, properties: dict[str, Any]) -> ConnectorResult:
        try:
            cid = self._contact_id(contact_id)
            if not cid:
                return ConnectorResult.failure(f"No HubSpot contact matching {contact_id!r}")
            self._run("HUBSPOT_UPDATE_CONTACT", {"contactId": cid, "properties": properties})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": cid, **properties}, summary="HubSpot contact updated",
                               metadata={"provider": self.provider})

    def tag_lead(self, contact_id: str, *, add_tags: Optional[list[str]] = None, remove_tags: Optional[list[str]] = None,
                 lifecycle: Optional[str] = None) -> ConnectorResult:
        """HubSpot has no contact tags: the lifecycle stage is set on the contact and the tags are recorded as a note."""
        changed = []
        if lifecycle:
            result = self._update_contact(contact_id, {"lifecyclestage": lifecycle})
            if not result.ok:
                return result
            changed.append(f"lifecycle {lifecycle}")
        if add_tags or remove_tags:
            parts = ([f"added {', '.join(add_tags)}"] if add_tags else []) + ([f"removed {', '.join(remove_tags)}"] if remove_tags else [])
            try:
                cid = self._contact_id(contact_id)
            except ConnectorError as exc:
                return ConnectorResult.failure(str(exc))
            note = self.create_note(body=f"Tags {'; '.join(parts)}.", contact_id=cid)
            if not note.ok:
                return note
            changed.append("tags recorded as a HubSpot note (HubSpot has no contact tags)")
        if not changed:
            return ConnectorResult.failure("Say which tags to add or remove, or which lifecycle stage to set.")
        return ConnectorResult(ok=True, data={"id": contact_id, "tags_added": add_tags or [], "tags_removed": remove_tags or [],
                                              "lifecycle": lifecycle}, summary="; ".join(changed),
                               metadata={"provider": self.provider})

    def update_contact_field(self, contact_id: str, *, field: str, value: Any) -> ConnectorResult:
        prop = _HS_CONTACT_FIELDS.get(field)
        if prop is None:
            return ConnectorResult.failure(f"{field!r} is not an updatable contact field. Allowed: {sorted(_HS_CONTACT_FIELDS)}")
        result = self._update_contact(contact_id, {prop: value})
        if result.ok:
            result.data = {"id": result.data["id"], field: value}
            result.summary = f"HubSpot contact {field} set to {value!r}"
        return result

    def create_contact(self, *, first_name: str, last_name: str = "", email: Optional[str] = None, company: Optional[str] = None,
                       title: Optional[str] = None, lifecycle: str = "lead", tags: Optional[list[str]] = None,
                       properties: Optional[dict[str, Any]] = None) -> ConnectorResult:
        args = {k: v for k, v in {"firstname": first_name, "lastname": last_name, "email": (email or "").lower() or None,
                                  "company": company, "jobtitle": title, "lifecyclestage": lifecycle}.items() if v}
        try:
            data = _hs_unwrap(self._run("HUBSPOT_CREATE_CONTACT", args))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        contact_id = str(data.get("id")) if isinstance(data, dict) and data.get("id") else None
        return ConnectorResult(ok=True, data={"id": contact_id}, summary=f"HubSpot contact {first_name} {last_name} created".strip(),
                               metadata={"provider": self.provider})

    def update_deal_stage(self, deal_id: str, *, stage: str) -> ConnectorResult:
        hs_stage = _HS_STAGES.get((stage or "").strip().lower(), stage)
        try:
            self._run("HUBSPOT_UPDATE_DEAL", {"dealId": str(deal_id), "properties": {"dealstage": hs_stage}})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": deal_id, "stage": hs_stage}, summary=f"HubSpot deal moved to {hs_stage}",
                               metadata={"provider": self.provider})

    def bulk_update_contacts(self, *, lifecycle_filter: Optional[str], field: str, value: Any) -> ConnectorResult:
        prop = _HS_CONTACT_FIELDS.get(field)
        if prop is None:
            return ConnectorResult.failure(f"{field!r} is not an updatable contact field")
        filters = [{"propertyName": "lifecyclestage", "operator": "EQ", "value": lifecycle_filter}] if lifecycle_filter else None
        try:
            rows, _ = self._search("HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA", filters=filters, properties=["email"], limit=100)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        updated, failed = 0, 0
        for row in rows:
            try:
                self._run("HUBSPOT_UPDATE_CONTACT", {"contactId": str(row.get("id")), "properties": {prop: value}})
                updated += 1
            except ConnectorError:
                failed += 1
        return ConnectorResult(ok=failed == 0, data={"updated": updated, "failed": failed},
                               summary=f"{updated} HubSpot contact(s) updated" + (f", {failed} failed" if failed else ""),
                               metadata={"provider": self.provider})

    def delete_record(self, *, record_type: str, record_id: str) -> ConnectorResult:
        actions = {"contact": ("HUBSPOT_ARCHIVE_CONTACT", "contactId"), "deal": ("HUBSPOT_REMOVE_DEAL", "dealId")}
        if record_type not in actions:
            return ConnectorResult.failure(f"Deleting a HubSpot {record_type} is not supported here; only contacts and deals.")
        action, key = actions[record_type]
        try:
            self._run(action, {key: str(record_id)})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"deleted": 1}, summary=f"HubSpot {record_type} {record_id} archived",
                               metadata={"provider": self.provider})


CONNECTORS = {
    "gmail": ComposioGmailConnector,
    "googlecalendar": ComposioCalendarConnector,
    "youtube": ComposioYouTubeConnector,
    "hubspot": ComposioHubSpotConnector,
}
