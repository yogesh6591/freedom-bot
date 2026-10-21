"""The workspace timezone setting: default, backfill for existing workspaces, and changing it."""

from __future__ import annotations

from sqlalchemy import text

from bizos.control import store as control
from bizos.control.db import control_connection, migrate_control_plane
from bizos.control.models import ClientSettings
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope, guarded_call
from bizos.util.timeutil import DEFAULT_TIMEZONE, is_timezone, local_now, workspace_zone


def _admin_call(client, ctx_factory, **args):
    scope = RunScope(ctx=ctx_factory(client, "admin"), settings=control.get_client(client.id).settings,
                     domain="general")
    with tenant_scope(scope.ctx):
        return guarded_call(scope, "admin_update_settings", args)


def _raw_settings(client_id):
    with control_connection() as conn:
        return conn.execute(text("SELECT settings FROM clients WHERE id = :i"), {"i": client_id}).scalar()


def _set_raw(client_id, settings):
    import json

    with control_connection() as conn:
        conn.execute(text("UPDATE clients SET settings = CAST(:s AS JSONB) WHERE id = :i"),
                     {"s": json.dumps(settings), "i": client_id})


def test_the_default_is_india_and_round_trips():
    assert ClientSettings().timezone == DEFAULT_TIMEZONE == "Asia/Kolkata"
    assert ClientSettings.from_dict({}).timezone == "Asia/Kolkata"
    assert ClientSettings.from_dict({"timezone": "Europe/London"}).timezone == "Europe/London"
    assert ClientSettings.from_dict(ClientSettings(timezone="Asia/Dubai").to_dict()).timezone == "Asia/Dubai"


def test_an_invalid_stored_name_falls_back_to_the_default():
    for bad in ("", "Mars/Olympus", "../etc/passwd", None, " Asia/Kolkata"):
        assert not is_timezone(bad)
        assert str(workspace_zone(ClientSettings(timezone=bad))) == "Asia/Kolkata"
    assert local_now(ClientSettings(timezone="America/New_York")).tzinfo is not None
    assert local_now(ClientSettings()).utcoffset().total_seconds() == 5.5 * 3600


def test_the_migration_sets_the_default_only_where_it_is_missing(client_a, client_scratch):
    before_a, before_s = _raw_settings(client_a.id), _raw_settings(client_scratch.id)
    try:
        _set_raw(client_a.id, {k: v for k, v in before_a.items() if k != "timezone"})
        _set_raw(client_scratch.id, {**before_s, "timezone": "Europe/London"})
        migrate_control_plane()
        assert _raw_settings(client_a.id)["timezone"] == "Asia/Kolkata"
        assert _raw_settings(client_scratch.id)["timezone"] == "Europe/London"
        # Nothing else in the row changes.
        assert {k: v for k, v in _raw_settings(client_a.id).items() if k != "timezone"} == \
            {k: v for k, v in before_a.items() if k != "timezone"}
    finally:
        _set_raw(client_a.id, before_a)
        _set_raw(client_scratch.id, before_s)


def test_an_admin_changes_it_with_admin_update_settings(client_scratch, ctx_factory):
    before = control.get_client(client_scratch.id).settings.timezone
    try:
        out = _admin_call(client_scratch, ctx_factory, timezone="Asia/Dubai")
        assert out.ok, out.message
        assert control.get_client(client_scratch.id).settings.timezone == "Asia/Dubai"

        bad = _admin_call(client_scratch, ctx_factory, timezone="IST")
        assert not bad.ok and "Unknown timezone" in bad.message
        assert control.get_client(client_scratch.id).settings.timezone == "Asia/Dubai"
    finally:
        _admin_call(client_scratch, ctx_factory, timezone=before)


def test_only_admins_can_change_it(client_scratch, ctx_factory):
    before = control.get_client(client_scratch.id).settings.timezone
    for who in ("operator", "approver"):
        scope = RunScope(ctx=ctx_factory(client_scratch, who), settings=control.get_client(client_scratch.id).settings,
                         domain="general")
        with tenant_scope(scope.ctx):
            out = guarded_call(scope, "admin_update_settings", {"timezone": "UTC"})
        assert not out.ok, who
    assert control.get_client(client_scratch.id).settings.timezone == before



def test_the_current_prompt_gives_now_in_the_workspace_timezone(client_scratch, ctx_factory, monkeypatch):
    from datetime import datetime as real_datetime, timezone as tz

    import bizos.util.timeutil as timeutil
    from bizos.agents.instructions import render
    from bizos.types import ExecutionMode

    class Frozen(real_datetime):
        @classmethod
        def now(cls, zone=None):
            # 2026-10-21 19:00 UTC = 2026-10-22 00:30 IST.
            return real_datetime(2026, 10, 21, 19, 0, tzinfo=tz.utc).astimezone(zone or tz.utc)

    monkeypatch.setattr(timeutil, "datetime", Frozen)
    ctx = ctx_factory(client_scratch, "operator")
    india = render(ctx=ctx, settings=ClientSettings(), mode=ExecutionMode.WAIT_FOR_APPROVAL, domain="general")
    assert "Current date and time: Thursday 2026-10-22 00:30 (Asia/Kolkata, the workspace timezone)" in india
    assert "(UTC)" not in india
    london = render(ctx=ctx, settings=ClientSettings(timezone="Europe/London"),
                    mode=ExecutionMode.WAIT_FOR_APPROVAL, domain="general")
    assert "Wednesday 2026-10-21 20:00 (Europe/London" in london
