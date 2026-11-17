"""Integration sync (freedombot/sync): incremental Gmail, updates by content, owner-only search, forget on disconnect.

Fake connectors only: no request reaches Composio, Google, YouTube or HubSpot.
"""

from __future__ import annotations

from types import SimpleNamespace

from freedombot import settings
from freedombot.sync import scheduler, store
from freedombot.sync.runner import sync_user
from freedombot.tenancy.context import tenant_scope
from freedombot.tools.base import RunScope, guarded_call


def ok(rows):
    return SimpleNamespace(ok=True, data=rows, summary=f"{len(rows)} row(s)")


class FakeGmail:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def search_threads(self, query="", *, limit=20):
        self.queries.append(query)
        return ok(self.rows)


class FakeCalendar:
    def __init__(self, rows):
        self.rows = rows

    def list_events(self, *, start=None, end=None, limit=50):
        return ok(self.rows)


class Broken:
    def search_contacts(self, *a, **k):
        raise RuntimeError("HubSpot is down")


def mail(mid, when, subject="Hello", sender="priya@globex.com"):
    return {"message_id": mid, "thread_id": f"t{mid}", "subject": subject, "sender": sender, "to": "me@acme.com",
            "sent_at": when, "body": f"Body of {subject}"}


def connectors(**by_category):
    return lambda ctx, category: by_category.get(category)


def test_gmail_backfills_then_only_adds_newer_messages(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "operator")
    gmail = FakeGmail([mail("m1", "2026-10-26T09:00:00+00:00"), mail("m2", "2026-10-26T10:00:00+00:00")])
    first = sync_user(ctx, connector_for=connectors(email=gmail))
    assert first["gmail"]["new"] == 2 and gmail.queries[-1] == "newer_than:30d"

    gmail.rows.append(mail("m3", "2026-10-27T08:00:00+00:00", subject="Renewal"))
    second = sync_user(ctx, connector_for=connectors(email=gmail))
    assert second["gmail"] == {"fetched": 1, "new": 1, "updated": 0}  # m1, m2 are older than the checkpoint
    assert gmail.queries[-1] == "newer_than:2d"
    with tenant_scope(ctx):
        assert [r["title"] for r in store.search(ctx, "renewal")] == ["Renewal"]


def test_changed_calendar_events_update_and_unchanged_ones_are_skipped(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "drafter")
    event = {"id": "e1", "title": "Contoso steering call", "starts_at": "2026-10-28T15:00:00+05:30",
             "ends_at": "2026-10-28T15:45:00+05:30", "attendees": ["maria@contoso.com"]}
    calendar = FakeCalendar([event])
    assert sync_user(ctx, connector_for=connectors(calendar=calendar))["calendar"]["new"] == 1
    assert sync_user(ctx, connector_for=connectors(calendar=calendar))["calendar"] == {"fetched": 1, "new": 0, "updated": 0}
    calendar.rows = [{**event, "starts_at": "2026-10-28T16:00:00+05:30"}]  # moved
    assert sync_user(ctx, connector_for=connectors(calendar=calendar))["calendar"]["updated"] == 1


def test_records_are_visible_only_to_their_owner(client_scratch, ctx_factory):
    owner, other = ctx_factory(client_scratch, "approver"), ctx_factory(client_scratch, "viewer")
    sync_user(owner, connector_for=connectors(email=FakeGmail([mail("p1", "2026-10-27T07:00:00+00:00",
                                                                       subject="Private salary offer")])))
    with tenant_scope(owner):
        assert store.search(owner, "salary")
    with tenant_scope(other):
        assert store.search(other, "salary") == []
        assert store.search(other, "") == []


def test_one_failing_source_is_recorded_and_the_others_still_sync(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    report = sync_user(ctx, connector_for=connectors(
        email=FakeGmail([mail("a1", "2026-10-27T06:00:00+00:00")]), crm=Broken()))
    assert report["gmail"]["new"] == 1 and "HubSpot is down" in report["hubspot"]["error"]
    with tenant_scope(ctx):
        states = {s["source"]: s for s in store.status(ctx)}
    assert states["hubspot"]["last_error"] and states["gmail"]["items"] == 1


def test_disconnect_forgets_that_source(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "finance")
    sync_user(ctx, connector_for=connectors(email=FakeGmail([mail("f1", "2026-10-27T05:00:00+00:00")])))
    with tenant_scope(ctx):
        assert store.forget(ctx, ctx.user_id, "gmail") == 1
        assert store.search(ctx, "") == [] and store.get_checkpoint(ctx, ctx.user_id, "gmail") is None


def test_synced_search_tool_returns_only_the_callers_records(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "ops")
    sync_user(ctx, connector_for=connectors(email=FakeGmail([mail("o1", "2026-10-27T04:00:00+00:00",
                                                                     subject="Dock schedule")])))
    with tenant_scope(ctx):
        out = guarded_call(RunScope(ctx=ctx, settings=client_scratch.settings, domain="general"),
                           "synced_search", {"query": "dock"})
    assert out.ok and [r["title"] for r in out.data] == ["Dock schedule"]


def test_the_background_loop_stays_off_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "integration_sync_enabled", lambda: False)
    assert scheduler.start(run=lambda: None) is None
