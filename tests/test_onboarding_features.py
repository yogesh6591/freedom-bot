"""Onboarding branch: CRM records saved where each person chooses (FreedomBot by default, HubSpot or a
sheet in Google Drive), recurring scheduled tasks with duplicate-free posting, and only the eight supported
apps (Gmail, Calendar, Drive, Sheets, Docs, YouTube, HubSpot, Facebook).

Mocked tools only: nothing reaches Composio, HubSpot, Facebook or any Google service.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bizos import automation
from bizos.connectors import composio
from bizos.connectors import composio_actions as acts
from bizos.tenancy.context import tenant_scope


# --------------------------------------------------------------------------- own CRM


def test_crm_records_go_to_freedombot_when_nothing_is_connected(client_a, ctx_factory):
    from bizos import crm_storage
    from bizos.connectors.mock_crm import CrmConnector
    from bizos.connectors.registry import get_connector

    ctx = ctx_factory(client_a, "operator")
    with tenant_scope(ctx):
        crm = get_connector("crm", ctx=ctx)
        assert isinstance(crm, CrmConnector) and crm.storage == crm_storage.FREEDOMBOT
        # Even a saved choice of HubSpot falls back to FreedomBot while HubSpot is not connected.
        crm_storage.save(ctx, ctx.user_id, crm_storage.HUBSPOT)
        try:
            crm = get_connector("crm", ctx=ctx)
            assert isinstance(crm, CrmConnector) and "not connected now" in crm.storage_note
        finally:
            crm_storage.save(ctx, ctx.user_id, crm_storage.AUTO)


def test_the_place_follows_the_request_then_the_saved_choice_then_automatic(client_a, ctx_factory, monkeypatch):
    from bizos import crm_storage

    ctx = ctx_factory(client_a, "admin")
    links = {"freedombot": True, "hubspot": True, "google_drive": True}
    monkeypatch.setattr(crm_storage, "connected", lambda c, u: dict(links))
    with tenant_scope(ctx):
        try:
            assert crm_storage.resolve(ctx, ctx.user_id) == ("hubspot", "")  # automatic: HubSpot when connected
            crm_storage.save(ctx, ctx.user_id, "freedombot")
            assert crm_storage.resolve(ctx, ctx.user_id)[0] == "freedombot"
            assert crm_storage.resolve(ctx, ctx.user_id, "HubSpot")[0] == "hubspot"  # the request wins
            crm_storage.save(ctx, ctx.user_id, "google_drive")
            assert crm_storage.resolve(ctx, ctx.user_id)[0] == "google_drive"
            links["google_drive"] = False
            place, note = crm_storage.resolve(ctx, ctx.user_id)
            assert place == "freedombot" and "Google Drive" in note
            links["hubspot"] = False
            with pytest.raises(crm_storage.StorageUnavailable, match="not connected"):
                crm_storage.resolve(ctx, ctx.user_id, "hubspot")
            with pytest.raises(ValueError):
                crm_storage.resolve(ctx, ctx.user_id, "slack")
        finally:
            crm_storage.save(ctx, ctx.user_id, "auto")
    assert crm_storage.parse("Google Drive") == crm_storage.parse("sheets") == "google_drive"
    assert crm_storage.parse("database") == "freedombot" and crm_storage.parse("nowhere") is None


def test_a_place_that_is_not_connected_is_refused_before_any_approval(client_a, scope_factory):
    from bizos.tools.base import guarded_call

    operator = scope_factory(client_a, "operator", domain="sales")
    with tenant_scope(operator.ctx):
        out = guarded_call(operator, "crm_create_contact", {"first_name": "Ethan", "save_to": "hubspot"})
        assert not out.ok and out.action_id is None and "HubSpot is not connected" in out.message
        bad = guarded_call(operator, "crm_create_company", {"name": "X Co", "save_to": "dropbox"})
        assert not bad.ok and "FreedomBot, HubSpot or Google Drive" in bad.message
        unchanged = guarded_call(operator, "crm_set_storage", {"destination": "google_drive"})
        assert not unchanged.ok and unchanged.action_id is None and "not connected" in unchanged.message
        where = guarded_call(operator, "crm_get_storage", {})
    assert where.ok and "FreedomBot" in where.message


def test_records_saved_in_google_drive_are_rated_high_risk(client_a, scope_factory, monkeypatch):
    from app.assistant import company_tools
    from bizos import crm_storage
    from bizos.types import RiskLevel

    scope = scope_factory(client_a, "operator", domain="sales")
    monkeypatch.setattr(crm_storage, "connected", lambda c, u: {"freedombot": True, "hubspot": False,
                                                                "google_drive": True})
    with tenant_scope(scope.ctx):
        assert company_tools._storage_risk(scope, {"save_to": "google_drive"}) == {"risk_override": RiskLevel.HIGH}
        assert company_tools._storage_risk(scope, {}) == {}  # automatic: FreedomBot while HubSpot is off


class _FakeSheets:
    """Google Sheets through Composio, in memory: one spreadsheet, tabs of rows."""

    def __init__(self):
        self.tabs, self.calls = {}, []

    def __call__(self, ctx, toolkit, action, arguments):
        assert toolkit == "googlesheets"
        self.calls.append((action, arguments))
        if action == "GOOGLESHEETS_SEARCH_SPREADSHEETS":
            return {"spreadsheets": []}
        if action == "GOOGLESHEETS_CREATE_GOOGLE_SHEET1":
            return {"response_data": {"spreadsheetId": "sheet_1", "spreadsheetUrl": "https://docs.example/sheet_1"}}
        if action == "GOOGLESHEETS_ADD_SHEET":
            self.tabs[arguments["title"]] = []
            return {}
        tab = arguments["range"].split("!")[0]
        if action == "GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND":
            assert arguments["valueInputOption"] == "RAW"  # never run as formulas
            self.tabs[tab] += arguments["values"]
            return {"updates": {"updatedRows": 1}}
        if action == "GOOGLESHEETS_VALUES_GET":
            return {"valueRange": {"values": [list(r) for r in self.tabs.get(tab, [])]}}
        raise AssertionError(action)


def test_crm_records_in_a_google_drive_sheet(client_a, ctx_factory, monkeypatch):
    from bizos import crm_storage
    from bizos.connectors.drive_crm import DriveSheetCrmConnector

    ctx = ctx_factory(client_a, "employee")
    fake = _FakeSheets()
    monkeypatch.setattr(composio, "execute", fake)
    with tenant_scope(ctx):
        crm = DriveSheetCrmConnector(ctx, {"owner_user_id": ctx.user_id})
        try:
            first = crm.create_company(name="QA NovaTech Solutions", domain="https://www.qa-novatech.example.com/")
            again = crm.create_company(name="Other name", domain="qa-novatech.example.com")
            assert first.ok and not first.data["existing"] and again.data["existing"]
            assert again.data["id"] == first.data["id"]
            person = crm.create_contact(first_name="Ethan", last_name="Parker", email="Ethan@QA-NovaTech.example.com",
                                        company="=IMPORTXML(1)")
            assert crm.create_contact(first_name="E", email="ethan@qa-novatech.example.com").data["existing"]
            deal = crm.create_deal(name="NovaTech AI Project", amount="$25,000", company_id=first.data["id"],
                                   contact_id="ethan@qa-novatech.example.com", close_date="2026-12-31")
            assert deal.ok and deal.data["contact_id"] == person.data["id"]
            assert crm.create_deal(name="novatech ai project", amount=1, company_id=first.data["id"]).data["existing"]
            assert not crm.create_deal(name="Y", amount=5, company_id="co_missing").ok
            assert crm.search_deals().data[0]["amount"] == "25000"
            assert crm.pipeline_summary().data["total_value"] == 25000.0
            assert crm.search_contacts("ethan").data[0]["company"] == "=IMPORTXML(1)"  # kept as text
            assert crm.create_note(body="Called Ethan", contact_id=person.data["id"]).ok
            assert crm.get_contact("ethan@qa-novatech.example.com").data["notes"][0]["body"] == "Called Ethan"
            refused = crm.update_deal_stage(deal.data["id"], stage="won")
            assert not refused.ok and "sheet itself" in refused.error
            # The sheet is made once and remembered.
            assert [a for a, _ in fake.calls].count("GOOGLESHEETS_CREATE_GOOGLE_SHEET1") == 1
            assert crm_storage.sheet(ctx, ctx.user_id) == ("sheet_1", "https://docs.example/sheet_1")
        finally:
            crm_storage.remember_sheet(ctx, ctx.user_id, None, None)


def test_hubspot_companies_and_deals_are_not_duplicated(client_a, ctx_factory, monkeypatch):
    calls = []
    existing = {"deals": []}

    def fake(ctx, toolkit, action, arguments):
        calls.append((action, arguments))
        if action == "HUBSPOT_RETRIEVE_PAGE_OF_CRM_OWNERS":
            return {"results": []}
        if action == "HUBSPOT_SEARCH_COMPANIES":
            return {"results": [{"id": "77", "properties": {"name": "QA NovaTech Solutions",
                                                            "domain": "qa-novatech.example.com"}}]}
        if action == "HUBSPOT_SEARCH_DEALS":
            return {"results": existing["deals"]}
        if action == "HUBSPOT_CREATE_DEAL":
            return {"id": "901"}
        raise AssertionError(action)

    monkeypatch.setattr(composio, "execute", fake)
    composio._hs_owner_cache.clear()
    ctx = ctx_factory(client_a, "employee")
    with tenant_scope(ctx):
        hs = composio.ComposioHubSpotConnector(ctx, {"owner_user_id": ctx.user_id})
        company = hs.create_company(name="qa novatech solutions")
        assert company.data["existing"] and company.data["id"] == "77"
        deal = hs.create_deal(name="NovaTech AI Project", amount=25000, stage="proposal", company_id="77",
                              close_date="2026-12-31")
        assert deal.ok and deal.data["id"] == "901"
        sent = next(a for name, a in calls if name == "HUBSPOT_CREATE_DEAL")
        assert sent["dealstage"] == "presentationscheduled" and sent["closedate"] == "2026-12-31T00:00:00Z"
        assert sent["associations"][0]["types"][0]["associationTypeId"] == 5
        existing["deals"] = [{"id": "901", "properties": {"dealname": "NovaTech AI Project"}}]
        assert hs.create_deal(name="novatech ai project", amount=1).data["existing"]
    composio._hs_owner_cache.clear()


def test_companies_and_deals_are_saved_once(client_a, ctx_factory):
    from bizos.connectors.mock_crm import CrmConnector

    ctx = ctx_factory(client_a, "operator")
    with tenant_scope(ctx):
        crm = CrmConnector(ctx, {})
        first = crm.create_company(name="QA NovaTech Solutions", domain="https://www.QA-NovaTech.example.com/about",
                                   industry="Information Technology")
        again = crm.create_company(name="qa novatech solutions")
        by_domain = crm.create_company(name="NovaTech", domain="qa-novatech.example.com")
        assert first.ok and first.data["existing"] is False and first.data["domain"] == "qa-novatech.example.com"
        assert again.data["id"] == by_domain.data["id"] == first.data["id"] and again.data["existing"] is True

        contact = crm.create_contact(first_name="Ethan", last_name="Parker",
                                     email="ethan.parker@qa-novatech.example.com", company="QA NovaTech Solutions")
        deal = crm.create_deal(name="QA NovaTech AI Automation Project", amount="25,000", stage="qualified",
                               close_date="2026-12-31", company_id=first.data["id"],
                               contact_id="ethan.parker@qa-novatech.example.com")
        assert deal.ok and deal.data["existing"] is False
        assert deal.data["contact_id"] == contact.data["id"] and deal.data["amount"] == "25000"
        repeat = crm.create_deal(name="QA NovaTech AI Automation Project", amount=1, company_id=first.data["id"])
        assert repeat.data["id"] == deal.data["id"] and repeat.data["existing"] is True

        assert not crm.create_deal(name="X", amount=5, company_id="co_missing").ok
        assert not crm.create_deal(name="X", amount="lots").ok
        assert not crm.create_deal(name="X", amount=5, close_date="next week").ok


def test_new_crm_tools_go_through_the_policy_check(client_a, scope_factory):
    from bizos.tools.base import guarded_call
    from bizos.types import PolicyEffect

    viewer = scope_factory(client_a, "viewer", domain="sales")
    with tenant_scope(viewer.ctx):
        refused = guarded_call(viewer, "crm_create_company", {"name": "Viewer Co"})
    assert not refused.ok and refused.effect == PolicyEffect.DENY

    operator = scope_factory(client_a, "operator", domain="sales")  # the default mode waits for approval
    with tenant_scope(operator.ctx):
        queued = guarded_call(operator, "crm_create_deal", {"name": "Queued deal", "amount": 100})
    assert queued.effect == PolicyEffect.REQUIRE_APPROVAL and queued.action_id


# --------------------------------------------------------------------------- scheduled tasks


def test_next_run_for_daily_and_weekly_schedules():
    wednesday_10am_ist = datetime(2026, 11, 11, 4, 30, tzinfo=timezone.utc)  # Wed 10:00 in Kolkata
    daily = automation.next_run("daily", "09:00", "Asia/Kolkata", after=wednesday_10am_ist)
    assert daily == datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)  # Thu 09:00 IST
    weekly = automation.next_run("weekly", "18:30", "Asia/Kolkata", weekdays=("mon", "thu"),
                                 after=wednesday_10am_ist)
    assert weekly == datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)  # Thu 18:30 IST
    assert automation.parse_time("6:30 pm") == "18:30" and automation.parse_time("12 am") == "00:00"
    assert automation.parse_weekdays(["Monday", "thu"]) == ("mon", "thu")
    for bad in ("25:00", "noon"):
        with pytest.raises(automation.ScheduleError):
            automation.parse_time(bad)


def test_every_n_minutes_schedules_keep_their_beat_and_are_never_under_five_minutes(client_a, ctx_factory):
    assert automation.parse_every(5) == 5 and automation.parse_every("50 min") == 50
    assert automation.parse_every("2 hours") == 120 and automation.parse_every("1.5h") == 90
    for too_often in (2, "1 minute", 0):
        with pytest.raises(automation.ScheduleError, match="every 5 minutes"):
            automation.parse_every(too_often)
    owner = ctx_factory(client_a, "operator")
    set_up_at = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    with tenant_scope(owner):
        task, created = automation.create(owner, title="AI news", instruction=f"Post AI news {set_up_at.timestamp()}",
                                          frequency="interval", every_minutes=5, time_of_day=None, tz="UTC",
                                          now=set_up_at)
        again, created_again = automation.create(owner, title="AI news", instruction=task["instruction"],
                                                 frequency="interval", every_minutes="5 min", time_of_day="",
                                                 tz="UTC", now=set_up_at)
        hourly, _ = automation.create(owner, title="x", instruction=f"Hourly {set_up_at.timestamp()}",
                                      frequency="interval", every_minutes=120, time_of_day="", tz="UTC")
    assert created and not created_again and again["id"] == task["id"]
    assert task["schedule"] == "every 5 minutes" and hourly["schedule"] == "every 2 hours"
    first = datetime.fromisoformat(task["next_run_at"])
    assert first == set_up_at + timedelta(minutes=5)
    runs = []
    runner = lambda ctx, settings, row: runs.append(row["id"]) or "posted"  # noqa: E731
    # Picked up 40 seconds late: the next run stays on the beat (10:10, not 10:10:40).
    automation.run_due(runner, now=first + timedelta(seconds=40), clients=[client_a])
    with tenant_scope(owner):
        after = automation.get(owner, task["id"])
    assert datetime.fromisoformat(after["next_run_at"]) == first + timedelta(minutes=5)
    # The server was down for 23 minutes: one catch-up run, then back on the beat.
    automation.run_due(runner, now=first + timedelta(minutes=28), clients=[client_a])
    with tenant_scope(owner):
        after = automation.get(owner, task["id"])
        for t in (task, hourly):
            automation.cancel(owner, t["id"])
    assert runs.count(task["id"]) == 2
    assert datetime.fromisoformat(after["next_run_at"]) == first + timedelta(minutes=30)


def test_an_interval_schedule_from_chat_needs_five_minutes_or_more(client_a, scope_factory):
    from bizos.tools.base import guarded_call

    scope = scope_factory(client_a, "admin", domain="general")
    with tenant_scope(scope.ctx):
        too_often = guarded_call(scope, "schedule_create", {"instruction": "Post AI news on our Facebook Page",
                                                            "frequency": "interval", "every_minutes": 2})
        missing = guarded_call(scope, "schedule_create", {"instruction": "Post AI news on our Facebook Page",
                                                          "frequency": "interval"})
        no_time = guarded_call(scope, "schedule_create", {"instruction": "Summarise my inbox", "frequency": "daily"})
    assert not too_often.ok and "every 5 minutes" in too_often.message and "Nothing was saved" in too_often.message
    assert not missing.ok and "every_minutes" in missing.message
    assert not no_time.ok and "time must look like" in no_time.message


def test_schedules_are_saved_once_and_only_their_owner_or_an_admin_stops_them(client_a, ctx_factory):
    owner, other, admin = (ctx_factory(client_a, p) for p in ("operator", "drafter", "admin"))
    with tenant_scope(owner):
        task, created = automation.create(owner, title="Weekly tip", instruction="Post our weekly AI tip on Facebook",
                                          frequency="weekly", time_of_day="9am", weekdays=["mon"], tz="Asia/Kolkata")
        same, created_again = automation.create(owner, title="again", instruction="post our weekly AI tip on  facebook",
                                                frequency="weekly", time_of_day="09:00", weekdays=["Monday"],
                                                tz="Asia/Kolkata")
        assert created and not created_again and same["id"] == task["id"]
        assert task["schedule"] == "every Mon at 09:00 (Asia/Kolkata)"
        assert any(t["id"] == task["id"] for t in automation.list_tasks(owner))
    with tenant_scope(other):
        assert all(t["id"] != task["id"] for t in automation.list_tasks(other))
        with pytest.raises(automation.ScheduleError):
            automation.cancel(other, task["id"])
    with tenant_scope(admin):
        stopped = automation.cancel(admin, task["id"])
    assert stopped["status"] == "cancelled" and stopped["next_run_at"] is None


def test_a_due_task_runs_once_as_the_person_who_set_it_up(client_a, ctx_factory):
    owner = ctx_factory(client_a, "operator")
    set_up_at = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    with tenant_scope(owner):
        task, _ = automation.create(owner, title="Inbox summary", instruction=f"Summarise my inbox {set_up_at}",
                                    frequency="daily", time_of_day="09:00", tz="Asia/Kolkata", now=set_up_at)
    runs = []

    def runner(ctx, settings, row):
        runs.append((ctx.user_id, ctx.attributes.get("scheduled_task"), row["id"]))
        return "Summary sent"

    due_at = datetime.fromisoformat(task["next_run_at"]) + timedelta(minutes=1)
    first = automation.run_due(runner, now=due_at, clients=[client_a])
    second = automation.run_due(runner, now=due_at, clients=[client_a])
    assert task["id"] in first["ran"] and task["id"] not in second["ran"]
    assert runs == [(owner.user_id, task["id"], task["id"])]
    with tenant_scope(owner):
        after = automation.get(owner, task["id"])
        automation.cancel(owner, task["id"])
    assert after["last_status"] == "done" and after["last_result"] == "Summary sent" and after["run_count"] == 1
    assert datetime.fromisoformat(after["next_run_at"]) > due_at


def test_a_scheduled_run_cannot_set_up_more_schedules(client_a, scope_factory):
    from bizos.tools.base import guarded_call

    scope = scope_factory(client_a, "admin", domain="general")
    scheduled = dataclasses.replace(scope, ctx=dataclasses.replace(scope.ctx, attributes={"scheduled_task": "sch_1"}))
    with tenant_scope(scheduled.ctx):
        outcome = guarded_call(scheduled, "schedule_create",
                               {"instruction": "Post a tip", "frequency": "daily", "time": "09:00"})
    assert not outcome.ok and "cannot set up more schedules" in outcome.message
    with tenant_scope(scope.ctx):
        bad = guarded_call(scope, "schedule_create", {"instruction": "Post a tip", "frequency": "weekly", "time": "9"})
    assert not bad.ok and "weekdays" in bad.message


def test_a_schedule_approved_for_someone_else_is_theirs_and_its_status_shows_on_the_card(client_a, scope_factory):
    from bizos.actions import store as actions
    from bizos.actions.preview import content_for
    from bizos.tools.base import guarded_call
    from bizos.tools.executor import execute_approved_action

    operator = scope_factory(client_a, "operator", domain="general")
    stamp = datetime.now().timestamp()
    args = {"title": f"Weekly tip {stamp}", "instruction": f"Post our weekly AI tip {stamp}", "frequency": "weekly",
            "time": "09:00", "weekdays": ["mon"], "timezone": "Asia/Kolkata"}
    with tenant_scope(operator.ctx):
        asked = guarded_call(operator, "schedule_create", args)
        assert asked.effect.value == "REQUIRE_APPROVAL", asked.message
        waiting = content_for(actions.get_action(asked.action_id, ctx=operator.ctx), ctx=operator.ctx)
    assert waiting["kind"] == "schedule" and waiting["headline"] == f"Recurring task: Weekly tip {stamp}"
    assert waiting["schedule"]["status"] == "not_set_up"
    assert waiting["schedule"]["schedule"] == "every Mon at 09:00 (Asia/Kolkata)"

    admin = scope_factory(client_a, "admin", domain="general")
    with tenant_scope(admin.ctx):
        actions.approve(asked.action_id, settings=client_a.settings, ctx=admin.ctx)
        done = execute_approved_action(admin, asked.action_id)
        assert done.ok, done.message
        card = content_for(actions.get_action(asked.action_id, ctx=admin.ctx), ctx=admin.ctx)
    task = card["schedule"]
    assert task["id"].startswith("sch_") and task["status"] == "active" and task["next_run_at"]
    # The requester's schedule, checked as them on every run, never as the admin who approved it.
    assert task["created_by"] == operator.ctx.user_id and not task["made_by_run"]
    with tenant_scope(operator.ctx):
        automation.cancel(operator.ctx, task["id"])


@pytest.fixture(scope="module")
def app_client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_requests_made_by_a_scheduled_run_show_their_schedule_on_approvals(client_a, ctx_factory, app_client):
    from app.api.deps import run_scope
    from bizos.actions import store as actions
    from bizos.actions.preview import content_for
    from bizos.tools.base import guarded_call

    owner = ctx_factory(client_a, "operator")
    stamp = datetime.now().timestamp()
    with tenant_scope(owner):
        task, _ = automation.create(owner, title=f"Planning {stamp}", instruction=f"Book the weekly review {stamp}",
                                    frequency="daily", time_of_day="09:00", tz="UTC")
    run_ctx = dataclasses.replace(owner, attributes={"scheduled_task": task["id"]})
    scope = run_scope(run_ctx, client_a.settings, domain="planning")
    assert scope.workflow_id == task["id"]
    with tenant_scope(run_ctx):
        asked = guarded_call(scope, "calendar_create_event", {
            "title": f"Weekly review {stamp}", "starts_at": "2026-12-01T10:00:00+00:00",
            "ends_at": "2026-12-01T11:00:00+00:00"})
        assert asked.effect.value == "REQUIRE_APPROVAL", asked.message
        action = actions.get_action(asked.action_id, ctx=owner)
        card = content_for(action, ctx=owner)
    assert action.workflow_id == task["id"]
    assert card["schedule"]["id"] == task["id"] and card["schedule"]["made_by_run"]
    assert card["schedule"]["status"] == "active" and card["schedule"]["schedule"] == "every day at 09:00 (UTC)"
    # Typed in chat (no schedule): no schedule box.
    typed = run_scope(owner, client_a.settings, domain="planning")
    assert typed.workflow_id is None

    login = app_client.post("/api/auth/login", json={"email": f"operator@{client_a.slug}.example.com",
                                                     "password": "bizos-dev-password", "client_slug": client_a.slug})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    listed = app_client.get("/api/approvals/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next(r for r in listed.json()["items"] if r["id"] == task["id"])
    assert row["status"] == "active" and row["awaiting_approval"] >= 1 and row["session_id"] == task["session_id"]
    queue = app_client.get("/api/approvals?status=PENDING_APPROVAL", headers=headers).json()["items"]
    shown = next(a for a in queue if a["id"] == asked.action_id)
    assert shown["content"]["schedule"]["id"] == task["id"]
    with tenant_scope(owner):
        automation.cancel(owner, task["id"])


# --------------------------------------------------------------------------- duplicate-free posting


def test_the_same_post_to_the_same_page_is_refused(client_a, scope_factory, monkeypatch):
    from app.assistant import company_tools

    scope = scope_factory(client_a, "admin", domain="general")
    post = {"page_id": "pg_1", "message": f"Five AI tips for small teams {datetime.now().timestamp()}"}
    monkeypatch.setattr(acts, "check", lambda slug, tier: ("facebook", None))
    monkeypatch.setattr(acts, "run", lambda ctx, slug, arguments, tier: ({"id": "post_1"}, False))
    args = {"action": "FACEBOOK_CREATE_POST", "arguments": post}
    with tenant_scope(scope.ctx):
        assert company_tools._app_action_problem(scope, args, acts.DESTRUCTIVE) is None
        assert company_tools._run_app_action(scope, args, acts.DESTRUCTIVE).ok
        problem = company_tools._app_action_problem(
            scope, {"action": "FACEBOOK_CREATE_POST", "arguments": {**post, "message": post["message"].upper() + "  "}},
            acts.DESTRUCTIVE)
        assert problem and "already published" in problem and "Nothing was run" in problem
        # A different page, or different words, is a new post.
        assert automation.already_posted(scope.ctx, "facebook", {**post, "page_id": "pg_2"}) is None
        assert automation.already_posted(scope.ctx, "facebook", {**post, "message": "Something else"}) is None
    assert acts.is_new_public_post("FACEBOOK_CREATE_PHOTO_POST") and acts.is_new_public_post("YOUTUBE_POST_COMMENT")
    assert not acts.is_new_public_post("FACEBOOK_UPDATE_POST") and not acts.is_new_public_post("GMAIL_SEND_EMAIL")


# --------------------------------------------------------------------------- the eight apps


def test_only_the_eight_apps_can_be_connected_or_used(monkeypatch):
    assert set(composio.TOOLKITS) == {"gmail", "googlecalendar", "googledrive", "googlesheets", "googledocs",
                                      "youtube", "hubspot", "facebook"}
    for slug in ("slack", "notion", "salesforce"):
        with pytest.raises(ValueError, match="FreedomBot connects"):
            composio._check_toolkit(slug)
    assert acts.check("GOOGLESHEETS_BATCH_UPDATE", acts.WRITE) == ("googlesheets", None)
    assert acts.check("FACEBOOK_CREATE_POST", acts.DESTRUCTIVE) == ("facebook", None)
    assert acts.check("HUBSPOT_CREATE_CONTACT", acts.WRITE) == ("hubspot", None)
    assert acts.check("SLACK_SEND_MESSAGE", acts.DESTRUCTIVE)[0] is None

    def account(slug, ident):
        return SimpleNamespace(id=ident, toolkit=SimpleNamespace(slug=slug))

    listing = SimpleNamespace(items=[account("hubspot", "acc_h"), account("gmail", "acc_g"), account("slack", "acc_s")])
    client = SimpleNamespace(connected_accounts=SimpleNamespace(list=lambda **_: listing))
    monkeypatch.setattr(composio, "configured", lambda: True)
    monkeypatch.setattr(composio, "get_client", lambda: client)
    ctx = SimpleNamespace(client_id="c_test", user_id="u_test")
    composio._status_cache.pop(composio.composio_user_id(ctx), None)
    accounts = composio._active_accounts(ctx)
    composio._status_cache.pop(composio.composio_user_id(ctx), None)
    assert accounts["gmail"] == "acc_g" and accounts["hubspot"] == "acc_h" and "slack" not in accounts
    with pytest.raises(Exception, match="not available"):
        composio.execute(ctx, "slack", "SLACK_SEND_MESSAGE", {})
