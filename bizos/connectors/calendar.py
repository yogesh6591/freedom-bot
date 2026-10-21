"""
Calendar Connectors
===================

``WorkspaceCalendarConnector`` is a working calendar over the client's
``calendar_events`` table — real availability computation, real conflict
detection, real create/reschedule/cancel. It is what the approval-mode test
asserts against ("the calendar remains unchanged until an approver accepts").

``GoogleCalendarConnector`` is the live adapter over agno's
``GoogleCalendarTools``; not exercised here (no credentials), reported NOT TESTED.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any, Optional

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorNotConfigured, ConnectorResult
from bizos.connectors.untrusted import wrap_many
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id
from bizos.util.timeutil import workspace_zone


class WorkspaceCalendarConnector(Connector):
    category = "calendar"
    provider = "workspace"

    # -- times ----------------------------------------------------------------
    # Times are converted here, never by the database: a time without an offset is wall-clock time in the
    # workspace timezone, every stored time is timezone-aware, and times are returned in the workspace zone.

    def _zone(self) -> tzinfo:
        if not hasattr(self, "_zone_cached"):
            from bizos.control import store as control

            self._zone_cached = workspace_zone(control.get_client(self.ctx.client_id).settings)
        return self._zone_cached

    def _moment(self, value: Any) -> datetime:
        """An ISO time as an aware datetime; raises ValueError when it is not one."""
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return moment.replace(tzinfo=self._zone()) if moment.tzinfo is None else moment

    def _local(self, row: dict[str, Any]) -> dict[str, Any]:
        for key in ("starts_at", "ends_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].astimezone(self._zone()).isoformat()
        return row

    def health(self) -> ConnectorResult:
        with readonly_connection(self.ctx) as conn:
            count = conn.execute(text("SELECT count(*) FROM calendar_events")).scalar()
        return ConnectorResult(ok=True, data={"events": count}, summary=f"{count} events")

    # -- reads --------------------------------------------------------------

    def list_events(
        self, *, start: Optional[str] = None, end: Optional[str] = None, limit: int = 50
    ) -> ConnectorResult:
        clauses = ["status <> 'cancelled'"]
        params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
        try:
            if start:
                clauses.append("starts_at >= :start")
                params["start"] = self._moment(start)
            if end:
                clauses.append("starts_at <= :end")
                params["end"] = self._moment(end)
        except ValueError:
            return ConnectorResult.failure("start and end must be ISO datetimes")
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(
                    f"SELECT * FROM calendar_events WHERE {' AND '.join(clauses)} "
                    "ORDER BY starts_at LIMIT :limit"
                ),
                params,
            ).fetchall()
        # Titles and descriptions can be set by external invitees.
        events, findings = wrap_many(
            [self._local(dict(r._mapping)) for r in rows], source="calendar_event", text_field="description"
        )
        return ConnectorResult(
            ok=True, data=events, summary=f"{len(events)} event(s)", untrusted_findings=findings
        )

    def check_availability(
        self, *, start: str, end: str, slot_minutes: int = 30
    ) -> ConnectorResult:
        """Free windows in a range. Returns free/busy only, never event details."""
        try:
            window_start, window_end = self._moment(start), self._moment(end)
        except ValueError:
            return ConnectorResult.failure("start and end must be ISO datetimes")
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(
                    "SELECT starts_at, ends_at FROM calendar_events "
                    "WHERE status <> 'cancelled' AND ends_at > :s AND starts_at < :e ORDER BY starts_at"
                ),
                {"s": window_start, "e": window_end},
            ).fetchall()
        zone = self._zone()
        window_start, window_end = window_start.astimezone(zone), window_end.astimezone(zone)
        busy = [(r.starts_at.astimezone(zone), r.ends_at.astimezone(zone)) for r in rows]

        free: list[dict[str, str]] = []
        cursor = window_start
        for busy_start, busy_end in busy:
            if busy_start > cursor:
                gap = (busy_start - cursor).total_seconds() / 60
                if gap >= slot_minutes:
                    free.append({"start": cursor.isoformat(), "end": busy_start.isoformat()})
            cursor = max(cursor, busy_end)
        if window_end > cursor and (window_end - cursor).total_seconds() / 60 >= slot_minutes:
            free.append({"start": cursor.isoformat(), "end": window_end.isoformat()})

        return ConnectorResult(
            ok=True,
            data={"free": free, "busy_count": len(busy)},
            summary=f"{len(free)} free window(s), {len(busy)} busy block(s)",
        )

    # -- writes -------------------------------------------------------------

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
        try:
            start, end = self._moment(starts_at), self._moment(ends_at)
        except ValueError:
            return ConnectorResult.failure("starts_at and ends_at must be ISO datetimes")
        event_id = new_id("evt")
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO calendar_events (id, calendar_id, title, description, location, "
                    "starts_at, ends_at, attendees, organizer, created_by) VALUES "
                    "(:i, :c, :t, :d, :l, :s, :e, :a, :o, :u)"
                ),
                {
                    "i": event_id,
                    "c": calendar_id,
                    "t": title,
                    "d": description,
                    "l": location,
                    "s": start,
                    "e": end,
                    "a": list(attendees or []),
                    "o": self.ctx.email or self.ctx.user_id,
                    "u": self.ctx.user_id,
                },
            )
        return ConnectorResult(
            ok=True,
            data={"event_id": event_id, "title": title, "starts_at": start.astimezone(self._zone()).isoformat(),
                  "ends_at": end.astimezone(self._zone()).isoformat()},
            summary=f"event '{title}' created for {_shown(start, self._zone())}",
        )

    def reschedule_event(self, event_id: str, *, starts_at: str, ends_at: str) -> ConnectorResult:
        try:
            start, end = self._moment(starts_at), self._moment(ends_at)
        except ValueError:
            return ConnectorResult.failure("starts_at and ends_at must be ISO datetimes")
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(
                text("UPDATE calendar_events SET starts_at = :s, ends_at = :e, updated_at = NOW() WHERE id = :i"),
                {"s": start, "e": end, "i": event_id},
            )
            if result.rowcount == 0:
                return ConnectorResult.failure(f"No event {event_id!r}")
        return ConnectorResult(
            ok=True,
            data={"event_id": event_id, "starts_at": start.astimezone(self._zone()).isoformat()},
            summary=f"event moved to {_shown(start, self._zone())}",
        )

    def cancel_event(self, event_id: str) -> ConnectorResult:
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(
                text("UPDATE calendar_events SET status = 'cancelled', updated_at = NOW() WHERE id = :i"),
                {"i": event_id},
            )
            if result.rowcount == 0:
                return ConnectorResult.failure(f"No event {event_id!r}")
        return ConnectorResult(ok=True, data={"event_id": event_id}, summary="event cancelled")

    def count_events(self) -> int:
        with readonly_connection(self.ctx) as conn:
            return int(
                conn.execute(
                    text("SELECT count(*) FROM calendar_events WHERE status <> 'cancelled'")
                ).scalar()
                or 0
            )


def _shown(moment: datetime, zone: tzinfo) -> str:
    """A time as the user reads it, e.g. "Thu 22 Oct 15:00 (Asia/Kolkata)"."""
    return f"{moment.astimezone(zone).strftime('%a %d %b %H:%M')} ({zone})"


class GoogleCalendarConnector(Connector):
    """Live Google Calendar adapter over agno's ``GoogleCalendarTools``.

    Wired but not exercised here (no credentials). Reported NOT TESTED.
    """

    category = "calendar"
    provider = "google_calendar"

    def _tools(self) -> Any:
        credentials = self.config.get("credentials_path")
        if not credentials:
            raise ConnectorNotConfigured(
                "Google Calendar is not configured for this workspace. Connect it under "
                "Integrations (needs a Google OAuth credentials file)."
            )
        from agno.tools.googlecalendar import GoogleCalendarTools  # lazy: optional dependency

        return GoogleCalendarTools(credentials_path=credentials)

    def health(self) -> ConnectorResult:
        try:
            self._tools()
        except ConnectorNotConfigured as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, summary="google calendar configured")

    def list_events(self, *, start: Optional[str] = None, end: Optional[str] = None, limit: int = 50) -> ConnectorResult:
        tools = self._tools()
        raw = tools.list_events(limit=limit, date_from=start)
        events, findings = wrap_many(
            raw if isinstance(raw, list) else [{"description": str(raw)}],
            source="google_calendar",
            text_field="description",
        )
        return ConnectorResult(ok=True, data=events, untrusted_findings=findings)

    def create_event(self, *, title: str, starts_at: str, ends_at: str, attendees: Optional[list[str]] = None, **kwargs: Any) -> ConnectorResult:
        tools = self._tools()
        result = tools.create_event(
            title=title, start_datetime=starts_at, end_datetime=ends_at, attendees=attendees or []
        )
        return ConnectorResult(ok=True, data={"google": str(result)}, summary="google event created")
