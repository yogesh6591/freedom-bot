"""The workspace calendar reads times without an offset in the workspace timezone, in app code.

The test database (compose `db`, port 5434) runs in Etc/UTC, so every case uses a workspace zone that is not
UTC: a result that followed the database server's timezone would come out hours off.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from bizos.connectors.calendar import WorkspaceCalendarConnector
from bizos.control import store as control
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import readonly_connection


@pytest.fixture
def zone(client_scratch):
    before = control.get_client(client_scratch.id).settings

    def set_zone(name):
        control.update_client_settings(client_scratch.id, dataclasses.replace(before, timezone=name), updated_by="test")

    yield set_zone
    control.update_client_settings(client_scratch.id, before, updated_by="test")


def _stored_utc(ctx, event_id):
    with readonly_connection(ctx) as conn:
        row = conn.execute(text("SELECT starts_at, ends_at FROM calendar_events WHERE id = :i"), {"i": event_id}).first()
    return row.starts_at.astimezone(timezone.utc), row.ends_at.astimezone(timezone.utc)


def test_3pm_without_an_offset_is_3pm_in_the_workspace_zone(client_scratch, ctx_factory, zone):
    zone("Europe/Berlin")  # CET (UTC+1) in November
    ctx = ctx_factory(client_scratch, "operator")
    with tenant_scope(ctx):
        out = WorkspaceCalendarConnector(ctx).create_event(
            title="Review", starts_at="2026-11-05T15:00:00", ends_at="2026-11-05T15:30:00")
        assert out.ok, out.error
        assert _stored_utc(ctx, out.data["event_id"]) == (
            datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc), datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc))
        assert out.data["starts_at"] == "2026-11-05T15:00:00+01:00"
        assert "Thu 05 Nov 15:00 (Europe/Berlin)" in out.summary


def test_india_3pm_is_0930_utc(client_scratch, ctx_factory, zone):
    zone("Asia/Kolkata")
    ctx = ctx_factory(client_scratch, "operator")
    with tenant_scope(ctx):
        out = WorkspaceCalendarConnector(ctx).create_event(
            title="Standup", starts_at="2026-11-06T15:00:00", ends_at="2026-11-06T15:30:00")
        assert _stored_utc(ctx, out.data["event_id"])[0] == datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)
        assert out.data["starts_at"] == "2026-11-06T15:00:00+05:30"


def test_a_time_with_an_offset_is_kept_as_given(client_scratch, ctx_factory, zone):
    zone("America/New_York")
    ctx = ctx_factory(client_scratch, "operator")
    with tenant_scope(ctx):
        out = WorkspaceCalendarConnector(ctx).create_event(
            title="Call", starts_at="2026-11-07T10:00:00Z", ends_at="2026-11-07T10:30:00+00:00")
        assert _stored_utc(ctx, out.data["event_id"])[0] == datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
        assert out.data["starts_at"] == "2026-11-07T05:00:00-05:00"


def test_reschedule_list_and_availability_use_the_workspace_zone(client_scratch, ctx_factory, zone):
    zone("Asia/Dubai")  # UTC+4
    ctx = ctx_factory(client_scratch, "operator")
    with tenant_scope(ctx):
        cal = WorkspaceCalendarConnector(ctx)
        made = cal.create_event(title="Dubai sync", starts_at="2026-11-10T09:00:00", ends_at="2026-11-10T10:00:00")
        moved = cal.reschedule_event(made.data["event_id"], starts_at="2026-11-10T11:00:00", ends_at="2026-11-10T12:00:00")
        assert moved.ok and moved.data["starts_at"] == "2026-11-10T11:00:00+04:00"
        assert _stored_utc(ctx, made.data["event_id"])[0] == datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)

        listed = cal.list_events(start="2026-11-10T00:00:00", end="2026-11-10T23:59:00")
        mine = [e for e in listed.data if e["id"] == made.data["event_id"]]
        assert mine and mine[0]["starts_at"] == "2026-11-10T11:00:00+04:00"

        free = cal.check_availability(start="2026-11-10T10:00:00", end="2026-11-10T13:00:00")
        assert free.ok
        assert {"start": "2026-11-10T10:00:00+04:00", "end": "2026-11-10T11:00:00+04:00"} in free.data["free"]
        assert {"start": "2026-11-10T12:00:00+04:00", "end": "2026-11-10T13:00:00+04:00"} in free.data["free"]


def test_a_bad_time_is_refused_without_writing(client_scratch, ctx_factory, zone):
    zone("Asia/Kolkata")
    ctx = ctx_factory(client_scratch, "operator")
    with tenant_scope(ctx):
        cal = WorkspaceCalendarConnector(ctx)
        before = cal.count_events()
        out = cal.create_event(title="x", starts_at="tomorrow 3pm", ends_at="tomorrow 4pm")
        assert not out.ok and "ISO" in out.error and cal.count_events() == before
