"""Per-person CRM access (Direct changes, Needs approval, Draft only) and whose HubSpot login a change runs on.

Mocked connectors only: nothing reaches HubSpot.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.assistant import company_tools
from bizos.connectors import composio
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.policy.engine import evaluate
from bizos.policy.models import EFFECT_ORDER, PolicyRequest
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope, guarded_call
from bizos.tools.executor import approve_and_execute
from bizos.types import APP_AREAS, ExecutionMode, PolicyEffect, Role

CONNECTED = frozenset({"email", "calendar", "crm"})
MODES = list(ExecutionMode)
LEVEL_MODE = {"approval": "WAIT_FOR_APPROVAL", "draft": "DRAFT"}


def person(client, *, level=None, role=Role.OPERATOR, tags=APP_AREAS):
    user = control.create_user(client_id=client.id, email=f"{uuid4().hex[:10]}@{client.slug}.example.com",
                               password="test-only-password-1", display_name="Test", roles=(role,),
                               created_by="test", data_scopes=tags)
    if level:
        user = control.set_user_app_mode(user.id, "crm", LEVEL_MODE[level])
    return control.build_context(user)


def settings_in(client, mode: ExecutionMode, **extra) -> ClientSettings:
    return ClientSettings.from_dict({**client.settings.to_dict(), "default_execution_mode": str(mode), **extra})


def decide(ctx, settings, tool, arguments=None, **flags):
    request = PolicyRequest(ctx=ctx, settings=settings, tool=tool, domain="sales", connected_integrations=CONNECTED,
                            arguments=dict(arguments or {}), **flags)
    with tenant_scope(ctx):
        return evaluate(request)


# --------------------------------------------------------------------------- the policy check


@pytest.mark.parametrize("level,effect,rule", [
    ("draft", PolicyEffect.DRAFT_ONLY, "PERSON_APP_DRAFT"),
    ("approval", PolicyEffect.REQUIRE_APPROVAL, "PERSON_APP_APPROVAL"),
])
def test_a_level_holds_a_person_back_even_in_auto_mode(client_scratch, level, effect, rule):
    decision = decide(person(client_scratch, level=level), settings_in(client_scratch, ExecutionMode.AUTO_WITHIN_SCOPE),
                      "crm_create_note")
    assert decision.effect == effect and decision.rule == rule
    assert "an admin set your CRM access" in decision.reason


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("tool", ["crm_create_note", "crm_update_deal_stage", "crm_delete_record"])
def test_a_level_only_ever_narrows(client_scratch, mode, tool):
    """Whatever the workspace mode, no level ever gives a result looser than having no level."""
    settings = settings_in(client_scratch, mode)
    baseline = decide(person(client_scratch), settings, tool)
    assert decide(person(client_scratch, level=None), settings, tool).effect == baseline.effect
    for level in LEVEL_MODE:
        effect = decide(person(client_scratch, level=level), settings, tool).effect
        assert EFFECT_ORDER[effect] <= EFFECT_ORDER[baseline.effect], (mode, tool, level)


def test_direct_changes_run_straight_away_only_when_the_workspace_allows(client_scratch):
    auto = settings_in(client_scratch, ExecutionMode.AUTO_WITHIN_SCOPE)
    wait = settings_in(client_scratch, ExecutionMode.WAIT_FOR_APPROVAL)
    direct = person(client_scratch)
    assert decide(direct, auto, "crm_create_note").effect == PolicyEffect.ALLOW
    assert decide(direct, wait, "crm_create_note").rule == "MODE_WAIT_FOR_APPROVAL"


def test_reads_and_other_apps_are_not_affected(client_scratch):
    auto = settings_in(client_scratch, ExecutionMode.AUTO_WITHIN_SCOPE)
    held = person(client_scratch, level="draft")
    assert decide(held, auto, "crm_search_contacts").effect == PolicyEffect.ALLOW
    assert decide(held, auto, "calendar_create_event").rule not in {"PERSON_APP_DRAFT", "PERSON_APP_APPROVAL"}


def test_connected_app_hubspot_actions_follow_the_same_level(client_scratch):
    auto = settings_in(client_scratch, ExecutionMode.AUTO_WITHIN_SCOPE)
    held = person(client_scratch, level="draft")
    hubspot = decide(held, auto, "integration_write_action", {"action": "HUBSPOT_CREATE_CONTACT", "arguments": {}})
    assert hubspot.rule == "PERSON_APP_DRAFT"
    gmail = decide(held, auto, "integration_write_action", {"action": "GMAIL_CREATE_EMAIL_DRAFT", "arguments": {}})
    assert gmail.rule != "PERSON_APP_DRAFT"


def test_the_admin_skip_approval_option_does_not_lift_a_persons_level(client_scratch):
    skip = settings_in(client_scratch, ExecutionMode.WAIT_FOR_APPROVAL,
                       approval_policy={**client_scratch.settings.to_dict()["approval_policy"], "admins_skip_approval": True})
    admin = person(client_scratch, role=Role.ADMIN)
    assert decide(admin, skip, "crm_create_note").rule == "ADMIN_SKIPS_APPROVAL"
    held = person(client_scratch, role=Role.ADMIN, level="approval")
    assert decide(held, skip, "crm_create_note").effect == PolicyEffect.REQUIRE_APPROVAL


# --------------------------------------------------------------------------- approvals


@pytest.fixture
def fake_crm(monkeypatch):
    """The workspace CRM stands in for HubSpot; records whose login each CRM call used."""
    from bizos.tools import base as tools_base

    monkeypatch.setattr(tools_base, "connected_categories", lambda ctx=None: CONNECTED)
    logins = []
    real = company_tools.get_connector

    def recording(category, *, ctx=None, owner_user_id=None):
        logins.append(owner_user_id or (ctx.user_id if ctx else None))
        return real(category, ctx=ctx, owner_user_id=owner_user_id)

    monkeypatch.setattr(company_tools, "get_connector", recording)
    return logins


def _a_contact(ctx) -> str:
    from sqlalchemy import text

    from bizos.tenancy.registry import readonly_connection

    with readonly_connection(ctx) as conn:
        return conn.execute(text("SELECT id FROM crm_contacts ORDER BY id LIMIT 1")).scalar()


def test_an_approved_crm_change_runs_on_the_requesters_login(client_scratch, fake_crm):
    requester = person(client_scratch, level="approval")
    contact = _a_contact(requester)
    with tenant_scope(requester):
        raised = guarded_call(RunScope(ctx=requester, settings=client_scratch.settings, domain="sales"),
                              "crm_create_note", {"contact_id": contact, "body": "Called about renewal"})
    assert raised.effect == PolicyEffect.REQUIRE_APPROVAL and raised.action_id

    # The approver is held to Draft only for their own changes; releasing someone else's is not one.
    approver = person(client_scratch, role=Role.APPROVER, level="draft")
    fake_crm.clear()
    with tenant_scope(approver):
        out = approve_and_execute(RunScope(ctx=approver, settings=client_scratch.settings, domain="sales"),
                                  raised.action_id)
    assert out.ok, out.message
    assert fake_crm and set(fake_crm) == {requester.user_id}
    # In the demo CRM the note is from the requester too.
    from sqlalchemy import text

    from bizos.tenancy.registry import readonly_connection

    with readonly_connection(requester) as conn:
        author = conn.execute(text("SELECT author FROM crm_notes WHERE id = :i"), {"i": out.data["id"]}).scalar()
    assert author == requester.user_id


# --------------------------------------------------------------------------- HubSpot owner


class _FakeHubSpot:
    def __init__(self, owners):
        self.owners, self.calls = owners, []

    def __call__(self, ctx, toolkit, action, arguments):
        self.calls.append((ctx.user_id, action, dict(arguments)))
        if action == "HUBSPOT_RETRIEVE_PAGE_OF_CRM_OWNERS":
            if self.owners is None:
                raise composio.ConnectorError("owners switched off")
            return {"results": [o for o in self.owners if o["email"] == arguments.get("email")]}
        return {"id": "900"}


@pytest.fixture
def hubspot(monkeypatch):
    composio._hs_owner_cache.clear()

    def install(owners):
        fake = _FakeHubSpot(owners)
        monkeypatch.setattr(composio, "execute", fake)
        return fake

    yield install
    composio._hs_owner_cache.clear()


def _created(fake, action):
    return next(args for _, a, args in fake.calls if a == action)


def test_new_hubspot_records_get_the_requester_as_owner(client_scratch, hubspot):
    requester = person(client_scratch)
    approver = person(client_scratch, role=Role.APPROVER)
    fake = hubspot([{"id": "77", "email": control.get_user(requester.user_id).email}])
    connector = composio.ComposioHubSpotConnector(approver, {"owner_user_id": requester.user_id})
    assert connector.create_note(body="Renewal call").ok
    assert connector.create_contact(first_name="Mira", last_name="Rao").ok
    assert _created(fake, "HUBSPOT_CREATE_NOTE")["hubspot_owner_id"] == "77"
    assert _created(fake, "HUBSPOT_CREATE_CONTACT")["hubspot_owner_id"] == "77"
    # Every call ran on the requester's connection, and the owner was looked up once.
    assert {user for user, _, _ in fake.calls} == {requester.user_id}
    assert [a for _, a, _ in fake.calls].count("HUBSPOT_RETRIEVE_PAGE_OF_CRM_OWNERS") == 1


def test_a_named_assignee_is_kept(client_scratch, hubspot):
    me = person(client_scratch)
    fake = hubspot([{"id": "77", "email": control.get_user(me.user_id).email}])
    composio.ComposioHubSpotConnector(me, {}).create_task(title="Send quote", assignee="55")
    assert _created(fake, "HUBSPOT_CREATE_TASK")["hubspot_owner_id"] == "55"


@pytest.mark.parametrize("owners", [[], None])
def test_no_matching_owner_or_a_failed_lookup_still_makes_the_record(client_scratch, hubspot, owners):
    me = person(client_scratch)
    fake = hubspot(owners)
    assert composio.ComposioHubSpotConnector(me, {}).create_note(body="Hello").ok
    assert "hubspot_owner_id" not in _created(fake, "HUBSPOT_CREATE_NOTE")


# --------------------------------------------------------------------------- the Users page


@pytest.fixture(scope="module")
def api():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(api, client, prefix="admin@"):
    email = next(u.email for u in control.list_users(client.id) if u.email.startswith(prefix))
    token = api.post("/api/auth/login", json={"client_slug": client.slug, "email": email,
                                             "password": "bizos-dev-password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _row(api, headers, user_id):
    return next(u for u in api.get("/api/users", headers=headers).json()["items"] if u["id"] == user_id)


def test_an_admin_sets_the_level_and_sees_what_it_does(api, client_scratch):
    headers = _login(api, client_scratch)
    target = person(client_scratch)
    assert _row(api, headers, target.user_id)["app_access"] == {"crm": "direct"}

    r = api.patch(f"/api/users/{target.user_id}/app-access", json={"area": "crm", "level": "draft"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["app_access"] == {"crm": "draft"} and r.json()["app_access_result"]["crm"]["effect"] == "DRAFT_ONLY"
    assert control.build_context(control.get_user(target.user_id)).app_mode("crm") == ExecutionMode.DRAFT

    back = api.patch(f"/api/users/{target.user_id}/app-access", json={"area": "crm", "level": "direct"}, headers=headers)
    assert back.json()["app_access"] == {"crm": "direct"} and control.get_user(target.user_id).app_modes == {}


@pytest.mark.parametrize("body,code", [
    ({"area": "crm", "level": "anything"}, 400),
    ({"area": "payroll", "level": "draft"}, 404),
])
def test_bad_levels_are_refused(api, client_scratch, body, code):
    target = person(client_scratch)
    r = api.patch(f"/api/users/{target.user_id}/app-access", json=body, headers=_login(api, client_scratch))
    assert r.status_code == code and control.get_user(target.user_id).app_modes == {}


def test_only_an_admin_of_that_workspace_can_set_it(api, client_scratch, client_a):
    target = person(client_scratch)
    path = f"/api/users/{target.user_id}/app-access"
    assert api.patch(path, json={"area": "crm", "level": "draft"}, headers=_login(api, client_scratch, "operator@")).status_code == 403
    assert api.patch(path, json={"area": "crm", "level": "draft"}, headers=_login(api, client_a)).status_code == 404
    assert control.get_user(target.user_id).app_modes == {}
