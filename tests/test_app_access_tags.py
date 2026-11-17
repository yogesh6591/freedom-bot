"""Access tags for apps: CRM, Gmail and Calendar need a tag, by every route to that data.

Fake Composio and fake connectors only: nothing reaches HubSpot, Gmail or Google Calendar.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from freedombot.bootstrap import ensure_dev_users
from freedombot.connectors import composio, composio_actions as acts
from freedombot.control import store as control
from freedombot.domains.packs import enabled_packs
from freedombot.policy.engine import evaluate
from freedombot.policy.models import PolicyRequest
from freedombot.rbac.job_roles import JOB_ROLES, tags_after_change
from freedombot.rbac.registry import TOOL_SPECS
from freedombot.sync import store as sync_store
from freedombot.sync.runner import sync_user
from freedombot.tenancy.context import tenant_scope
from freedombot.tools.base import RunScope, guarded_call
from freedombot.tools.dry_run import is_visible
from freedombot.tools.executor import approve_and_execute
from freedombot.types import APP_AREAS, DATA_AREAS, ActionStatus, PolicyEffect, Role

CONNECTED = frozenset({"email", "calendar", "crm", "youtube"})
RESTRICTED = "DATA_AREA_RESTRICTED"

TOOLS = [
    ("crm_search_contacts", "crm"),
    ("crm_update_deal_stage", "crm"),
    ("email_search_threads", "email"),
    ("email_send_message", "email"),
    ("calendar_list_events", "calendar"),
    ("calendar_create_event", "calendar"),
]
APP_ACTIONS = [
    ("integration_read_action", "HUBSPOT_LIST_CONTACTS", "crm"),
    ("integration_write_action", "HUBSPOT_CREATE_CONTACT", "crm"),
    ("integration_read_action", "GMAIL_FETCH_EMAILS", "email"),
    ("integration_destructive_action", "GMAIL_SEND_EMAIL", "email"),
    ("integration_read_action", "GOOGLECALENDAR_LIST_EVENTS", "calendar"),
]


def person(client, tags=(), role=Role.OPERATOR):
    user = control.create_user(
        client_id=client.id,
        email=f"{uuid4().hex[:10]}@{client.slug}.example.com",
        password="test-only-password-1",
        display_name="Test",
        roles=(role,),
        created_by="test",
        data_scopes=tags,
    )
    return control.build_context(user)


def retag(ctx, tags):
    return control.build_context(control.set_user_scopes(ctx.user_id, list(tags)))


def decide(client, ctx, tool):
    request = PolicyRequest(ctx=ctx, settings=client.settings, tool=tool, domain="sales", connected_integrations=CONNECTED)
    with tenant_scope(ctx):
        return evaluate(request)


@pytest.fixture
def fake_apps(monkeypatch):
    from freedombot.tools import base as tools_base

    monkeypatch.setattr(tools_base, "connected_categories", lambda ctx=None: CONNECTED)
    ran = []
    monkeypatch.setattr(acts, "describe", lambda slug: {"tier": acts.classify_by_name(slug)})
    monkeypatch.setattr(composio, "execute", lambda c, tk, slug, args: ran.append(slug) or {"ok": True})
    return ran


def test_every_crm_email_and_calendar_tool_needs_its_tag():
    specs = [s for s in TOOL_SPECS if s.category in APP_AREAS]
    assert len(specs) == 26  # 22 + crm_create_company, crm_create_deal, crm_get_storage, crm_set_storage
    assert all(s.data_area == s.category for s in specs)
    assert set(APP_AREAS) <= set(DATA_AREAS)


@pytest.mark.parametrize("tool,area", TOOLS)
def test_a_tool_is_refused_without_its_tag_and_opens_with_it(client_scratch, tool, area):
    ctx = person(client_scratch, ("finance",))
    blocked = decide(client_scratch, ctx, tool)
    assert blocked.effect == PolicyEffect.DENY and blocked.rule == RESTRICTED

    assert decide(client_scratch, retag(ctx, ("finance", area)), tool).rule != RESTRICTED


@pytest.mark.parametrize("tool,area", TOOLS)
def test_one_tag_does_not_open_the_other_apps(client_scratch, tool, area):
    other = next(a for a in APP_AREAS if a != area)
    assert decide(client_scratch, person(client_scratch, (other,)), tool).rule == RESTRICTED


@pytest.mark.parametrize("tool,_area", TOOLS)
def test_admins_need_no_tags(client_scratch, ctx_factory, tool, _area):
    assert decide(client_scratch, ctx_factory(client_scratch, "admin"), tool).rule != RESTRICTED


def test_the_assistant_is_not_given_the_tool_without_the_tag(client_scratch, fake_apps):
    packs = enabled_packs(client_scratch.settings.enabled_domains)
    ctx = person(client_scratch)
    with tenant_scope(ctx):
        assert not is_visible(RunScope(ctx=ctx, settings=client_scratch.settings, domain="sales"), "crm_search_contacts", packs)
    tagged = retag(ctx, ("crm",))
    with tenant_scope(tagged):
        assert is_visible(RunScope(ctx=tagged, settings=client_scratch.settings, domain="sales"), "crm_search_contacts", packs)


@pytest.mark.parametrize("tool,action,area", APP_ACTIONS)
def test_connected_apps_follow_the_same_tag(client_scratch, fake_apps, tool, action, area):
    ctx = person(client_scratch)
    with tenant_scope(ctx):
        out = guarded_call(RunScope(ctx=ctx, settings=client_scratch.settings, domain="general"), tool,
                           {"action": action, "arguments": {}})
    assert out.effect == PolicyEffect.DENY and out.action_id is None and not fake_apps


def test_a_connected_app_runs_once_the_tag_is_given(client_scratch, fake_apps):
    ctx = person(client_scratch, ("crm",))
    with tenant_scope(ctx):
        out = guarded_call(RunScope(ctx=ctx, settings=client_scratch.settings, domain="general"),
                           "integration_read_action", {"action": "HUBSPOT_LIST_CONTACTS", "arguments": {}})
    assert out.ok and fake_apps == ["HUBSPOT_LIST_CONTACTS"]


def _synced(ctx):
    mail = SimpleNamespace(search_threads=lambda query="", *, limit=20: SimpleNamespace(ok=True, summary="", data=[
        {"message_id": f"m{ctx.user_id}", "thread_id": "t1", "subject": "Renewal", "sender": "a@b.com", "to": "me@b.com",
         "sent_at": "2026-10-27T04:00:00+00:00", "body": "Renewal terms"}]))
    calendar = SimpleNamespace(list_events=lambda *, start=None, end=None, limit=50: SimpleNamespace(ok=True, summary="", data=[
        {"id": f"e{ctx.user_id}", "title": "Renewal call", "starts_at": "2026-10-27T05:00:00+00:00"}]))
    sync_user(ctx, connector_for=lambda _ctx, category: {"email": mail, "calendar": calendar}.get(category))


def test_synced_records_follow_the_same_tags(client_scratch):
    ctx = person(client_scratch, ("email", "calendar"))
    _synced(ctx)
    with tenant_scope(ctx):
        assert {r["source"] for r in sync_store.search(ctx, "renewal")} == {"gmail", "calendar"}

    only_calendar = retag(ctx, ("calendar",))
    with tenant_scope(only_calendar):
        assert {r["source"] for r in sync_store.search(only_calendar, "renewal")} == {"calendar"}
        assert sync_store.search(only_calendar, "renewal", sources=["gmail"]) == []

    none = retag(ctx, ())
    with tenant_scope(none):
        assert sync_store.search(none, "") == []
        out = guarded_call(RunScope(ctx=none, settings=client_scratch.settings, domain="general"), "synced_search",
                           {"query": "renewal"})
    assert out.ok and not out.data


def test_a_restart_keeps_the_app_tags_an_admin_set(client_scratch):
    domain = f"{client_scratch.slug}.example.com"

    def tags():
        email = f"hr@{domain}"
        return next(u for u in control.list_users(client_scratch.id) if u.email == email)

    assert set(APP_AREAS) <= set(tags().data_scopes)  # a new demo account starts with them

    control.set_user_scopes(tags().id, ["hr", "salary", "crm"])
    ensure_dev_users(client_scratch, domain=domain)
    assert set(tags().data_scopes) == {"hr", "salary", "crm"}

    control.set_user_scopes(tags().id, ["finance"])  # the department tags are still put back
    ensure_dev_users(client_scratch, domain=domain)
    assert set(tags().data_scopes) == {"hr", "salary"}


def test_changing_role_keeps_app_tags():
    tags = tags_after_change({"sales", "crm", "email"}, JOB_ROLES["SALES"], JOB_ROLES["FINANCE"])
    assert tags == {"finance", "crm", "email"}


def test_an_approver_needs_the_tag_to_see_and_decide_the_action(client_scratch, fake_apps):
    from freedombot.actions import store as actions

    requester = person(client_scratch, ("crm",))
    with tenant_scope(requester):
        raised = guarded_call(RunScope(ctx=requester, settings=client_scratch.settings, domain="sales"),
                              "crm_create_contact", {"first_name": "Mira", "last_name": "Rao", "email": "mira@acme.com"})
    assert raised.effect == PolicyEffect.REQUIRE_APPROVAL and raised.action_id

    approver = person(client_scratch, (), role=Role.APPROVER)
    with tenant_scope(approver):
        assert raised.action_id not in {a.id for a in actions.list_actions(limit=200, ctx=approver)}
        with pytest.raises(actions.ActionNotFound):
            approve_and_execute(RunScope(ctx=approver, settings=client_scratch.settings, domain="sales"), raised.action_id)

    tagged = retag(approver, ("crm",))
    with tenant_scope(tagged):
        assert actions.get_action(raised.action_id, ctx=tagged).status == ActionStatus.PENDING_APPROVAL
        out = approve_and_execute(RunScope(ctx=tagged, settings=client_scratch.settings, domain="sales"), raised.action_id)
    assert out.ok
