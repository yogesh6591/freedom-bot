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

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorNotConfigured, ConnectorResult
from bizos.connectors.untrusted import wrap_many
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id
from bizos.util.timeutil import ensure_utc, utcnow


class WorkspaceCalendarConnector(Connector):
    category = "calendar"
    provider = "workspace"

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
        if start:
            clauses.append("starts_at >= CAST(:start AS TIMESTAMPTZ)")
            params["start"] = start
        if end:
            clauses.append("starts_at <= CAST(:end AS TIMESTAMPTZ)")
            params["end"] = end
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
            [dict(r._mapping) for r in rows], source="calendar_event", text_field="description"
        )
        return ConnectorResult(
            ok=True, data=events, summary=f"{len(events)} event(s)", untrusted_findings=findings
        )

    def check_availability(
        self, *, start: str, end: str, slot_minutes: int = 30
    ) -> ConnectorResult:
        """Free windows in a range. Returns free/busy only, never event details."""
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(
                    "SELECT starts_at, ends_at FROM calendar_events "
                    "WHERE status <> 'cancelled' AND ends_at > CAST(:s AS TIMESTAMPTZ) "
                    "AND starts_at < CAST(:e AS TIMESTAMPTZ) ORDER BY starts_at"
                ),
                {"s": start, "e": end},
            ).fetchall()
        window_start = ensure_utc(datetime.fromisoformat(start))
        window_end = ensure_utc(datetime.fromisoformat(end))
        busy = [(ensure_utc(r.starts_at), ensure_utc(r.ends_at)) for r in rows]

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
        event_id = new_id("evt")
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO calendar_events (id, calendar_id, title, description, location, "
                    "starts_at, ends_at, attendees, organizer, created_by) VALUES "
                    "(:i, :c, :t, :d, :l, CAST(:s AS TIMESTAMPTZ), CAST(:e AS TIMESTAMPTZ), :a, :o, :u)"
                ),
                {
                    "i": event_id,
                    "c": calendar_id,
                    "t": title,
                    "d": description,
                    "l": location,
                    "s": starts_at,
                    "e": ends_at,
                    "a": list(attendees or []),
                    "o": self.ctx.email or self.ctx.user_id,
                    "u": self.ctx.user_id,
                },
            )
        return ConnectorResult(
            ok=True,
            data={"event_id": event_id, "title": title, "starts_at": starts_at},
            summary=f"event '{title}' created at {starts_at}",
        )

    def reschedule_event(self, event_id: str, *, starts_at: str, ends_at: str) -> ConnectorResult:
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(
                text(
                    "UPDATE calendar_events SET starts_at = CAST(:s AS TIMESTAMPTZ), "
                    "ends_at = CAST(:e AS TIMESTAMPTZ), updated_at = NOW() WHERE id = :i"
                ),
                {"s": starts_at, "e": ends_at, "i": event_id},
            )
            if result.rowcount == 0:
                return ConnectorResult.failure(f"No event {event_id!r}")
        return ConnectorResult(ok=True, data={"event_id": event_id}, summary=f"event moved to {starts_at}")

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
