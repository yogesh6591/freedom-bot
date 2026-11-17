"""Whose CRM login an approved change runs on, and who owns new HubSpot records.

The per-person CRM level is gone: the person's one role (freedombot/policy/person_role.py) holds them back now.

Mocked connectors only: nothing reaches HubSpot.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.assistant import company_tools
from freedombot.connectors import composio
from freedombot.control import store as control
from freedombot.tenancy.context import tenant_scope
from freedombot.tools.base import RunScope, guarded_call
from freedombot.tools.executor import approve_and_execute
from freedombot.types import APP_AREAS, PolicyEffect, Role

CONNECTED = frozenset({"email", "calendar", "crm"})
LEVEL_ROLE = {"approval": "approve", "draft": "draft"}
SALES = (*APP_AREAS, "sales")


def person(client, *, level=None, role=Role.OPERATOR, tags=SALES):
    user = control.create_user(client_id=client.id, email=f"{uuid4().hex[:10]}@{client.slug}.example.com",
                               password="test-only-password-1", display_name="Test", roles=(role,),
                               created_by="test", data_scopes=tags)
    if level:
        user = control.set_user_access_mode(user.id, LEVEL_ROLE[level])
    return control.build_context(user)


# --------------------------------------------------------------------------- approvals


@pytest.fixture
def fake_crm(monkeypatch):
    """The workspace CRM (nothing connected); records whose login each CRM call used."""
    from freedombot.tools import base as tools_base

    monkeypatch.setattr(tools_base, "connected_categories", lambda ctx=None: CONNECTED)
    logins = []
    real = company_tools.get_connector

    def recording(category, *, ctx=None, owner_user_id=None, **kwargs):
        logins.append(owner_user_id or (ctx.user_id if ctx else None))
        return real(category, ctx=ctx, owner_user_id=owner_user_id, **kwargs)

    monkeypatch.setattr(company_tools, "get_connector", recording)
    return logins


def _a_contact(ctx) -> str:
    from sqlalchemy import text

    from freedombot.tenancy.registry import readonly_connection

    with readonly_connection(ctx) as conn:
        return conn.execute(text("SELECT id FROM crm_contacts ORDER BY id LIMIT 1")).scalar()


def test_an_approved_crm_change_runs_on_the_requesters_login(client_scratch, fake_crm):
    requester = person(client_scratch, level="approval")
    contact = _a_contact(requester)
    with tenant_scope(requester):
        raised = guarded_call(RunScope(ctx=requester, settings=client_scratch.settings, domain="sales"),
                              "crm_create_note", {"contact_id": contact, "body": "Called about renewal"})
    assert raised.effect == PolicyEffect.REQUIRE_APPROVAL and raised.action_id

    # The approver's own Sales role is Draft; releasing someone else's change is not their change.
    approver = person(client_scratch, role=Role.APPROVER, level="draft")
    fake_crm.clear()
    with tenant_scope(approver):
        out = approve_and_execute(RunScope(ctx=approver, settings=client_scratch.settings, domain="sales"),
                                  raised.action_id)
    assert out.ok, out.message
    assert fake_crm and set(fake_crm) == {requester.user_id}
    # In the demo CRM the note is from the requester too.
    from sqlalchemy import text

    from freedombot.tenancy.registry import readonly_connection

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
