"""The chat's approval inbox, and the Composio catalog with per-user tool choices.

Fake Composio only: nothing reaches Composio or any connected platform.
"""

from __future__ import annotations

import pytest

from app.api import approvals as approvals_api
from bizos.connectors import composio, composio_actions as acts, composio_catalog as catalog
from bizos.connectors.base import ConnectorError
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import guarded_call


def _propose(scope, title: str) -> str:
    with tenant_scope(scope.ctx):
        outcome = guarded_call(scope, "calendar_create_event", {
            "title": title, "starts_at": "2026-12-03T10:00:00+00:00", "ends_at": "2026-12-03T11:00:00+00:00"})
    assert outcome.action_id, outcome.message
    return outcome.action_id


# --------------------------------------------------------------------------- approval inbox


def test_an_approver_sees_others_requests_to_decide_and_the_requester_sees_their_own(client_a, scope_factory):
    proposer = scope_factory(client_a, "operator", domain="planning")
    action_id = _propose(proposer, "Inbox probe")
    approver = scope_factory(client_a, "approver", domain="planning")

    theirs = approvals_api.inbox(ctx=approver.ctx)
    assert theirs["can_decide"] and action_id in [a["id"] for a in theirs["to_decide"]]
    card = next(a for a in theirs["to_decide"] if a["id"] == action_id)
    assert card["content"]["headline"] and card["requested_by_name"]

    mine = approvals_api.inbox(ctx=proposer.ctx)
    assert action_id in [a["id"] for a in mine["mine"]]
    assert action_id not in [a["id"] for a in mine["to_decide"]]  # never asked to approve your own


def test_a_viewer_has_nothing_to_decide(client_a, scope_factory):
    viewer = scope_factory(client_a, "viewer", domain="planning")
    inbox = approvals_api.inbox(ctx=viewer.ctx)
    assert inbox["can_decide"] is False and inbox["to_decide"] == []


# --------------------------------------------------------------------------- catalog


@pytest.fixture
def fake_catalog(monkeypatch):
    items = [
        {"slug": "gmail", "name": "Gmail", "description": "Email", "logo": "", "categories": ["email"],
         "tools_count": 61, "managed": True, "auth_schemes": ["OAUTH2"]},
        {"slug": "slack", "name": "Slack", "description": "Team chat", "logo": "", "categories": ["communication"],
         "tools_count": 150, "managed": True, "auth_schemes": ["OAUTH2"]},
        {"slug": "serpapi", "name": "SerpApi", "description": "Search results", "logo": "", "categories": ["search"],
         "tools_count": 5, "managed": False, "auth_schemes": ["API_KEY"]},
        {"slug": "salesforce", "name": "Salesforce", "description": "CRM", "logo": "", "categories": ["crm"],
         "tools_count": 200, "managed": False, "auth_schemes": ["OAUTH2"]},
    ]
    monkeypatch.setattr(catalog, "catalog", lambda: items)
    monkeypatch.setattr(catalog, "configured", lambda: frozenset({"salesforce"}))
    return items


def test_search_finds_platforms_and_says_which_can_be_connected(fake_catalog):
    found = catalog.search("team", connected=frozenset())
    assert [p["slug"] for p in found["items"]] == ["slack"]
    every = {p["slug"]: p for p in catalog.search("", connected=frozenset({"gmail"}))["items"]}
    assert every["gmail"]["connected"] and every["slack"]["connectable"]
    assert every["salesforce"]["connectable"]  # set up in this Composio project
    assert not every["serpapi"]["connectable"]  # needs its own API key, set up by an admin
    assert catalog.search("", category="crm", connected=frozenset())["total"] == 1
    assert [p["slug"] for p in catalog.search("", connected=frozenset({"slack"}))["items"]][0] == "slack"


def test_a_platform_that_needs_admin_setup_is_not_offered_as_connectable(fake_catalog, client_a, ctx_factory, monkeypatch):
    monkeypatch.setattr(composio, "get_client", lambda: pytest.fail("no sign-in link for a platform that cannot connect"))
    with pytest.raises(ValueError, match="needs an admin to set it up"):
        composio.connect_url(ctx_factory(client_a, "admin"), "serpapi")


# --------------------------------------------------------------------------- tool choices


def test_all_tools_by_default_then_only_the_chosen_ones(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "operator")
    with tenant_scope(ctx):
        catalog.choose(ctx, "slack", None)
        assert catalog.chosen(ctx, "slack") is None and catalog.allowed(ctx, "slack", "SLACK_SEND_MESSAGE")
        catalog.choose(ctx, "slack", ["SLACK_LIST_CHANNELS"])
        assert catalog.allowed(ctx, "slack", "SLACK_LIST_CHANNELS")
        assert not catalog.allowed(ctx, "slack", "SLACK_SEND_MESSAGE")
        other = ctx_factory(client_a, "drafter")
        assert catalog.allowed(other, "slack", "SLACK_SEND_MESSAGE")  # choices are per user
        catalog.choose(ctx, "slack", None)
        assert catalog.allowed(ctx, "slack", "SLACK_SEND_MESSAGE")


def test_a_switched_off_tool_never_runs(client_a, ctx_factory, monkeypatch):
    ctx = ctx_factory(client_a, "operator")
    monkeypatch.setattr(composio, "_active_accounts", lambda c: {"gmail": "acc_1"})
    monkeypatch.setattr(composio, "get_client", lambda: pytest.fail("a switched-off tool must not reach Composio"))
    with tenant_scope(ctx):
        catalog.choose(ctx, "gmail", ["GMAIL_FETCH_EMAILS"])
        try:
            with pytest.raises(ConnectorError, match="switched off"):
                composio.execute(ctx, "gmail", "GMAIL_SEND_EMAIL", {})
        finally:
            catalog.choose(ctx, "gmail", None)


def test_find_hides_switched_off_tools_and_any_platform_works(client_a, ctx_factory, monkeypatch):
    from types import SimpleNamespace as NS

    ctx = ctx_factory(client_a, "operator")
    raw = [NS(slug="SLACK_LIST_CHANNELS", name="List channels", description="", tags=["readOnlyHint"],
              toolkit=NS(slug="slack"), input_parameters={}, is_deprecated=False),
           NS(slug="SLACK_SEND_MESSAGE", name="Send message", description="", tags=["createHint"],
              toolkit=NS(slug="slack"), input_parameters={}, is_deprecated=False)]
    monkeypatch.setattr(acts, "connected_toolkits", lambda c: ["slack"])
    monkeypatch.setattr(composio, "get_client", lambda: NS(tools=NS(
        get_raw_composio_tools=lambda **k: raw,
        get_raw_composio_tool_by_slug=lambda slug: next(t for t in raw if t.slug == slug))))
    with tenant_scope(ctx):
        catalog.choose(ctx, "slack", ["SLACK_LIST_CHANNELS"])
        try:
            found = acts.find(ctx, "slack channels")
            assert [a["action_id"] for a in found["actions"]] == ["SLACK_LIST_CHANNELS"]
            assert found["actions"][0]["app"] == "slack"
            # A platform outside the four built-in apps is runnable with the matching tier.
            assert acts.check("SLACK_SEND_MESSAGE", acts.DESTRUCTIVE) == ("slack", None)
        finally:
            catalog.choose(ctx, "slack", None)


def test_find_ranks_plain_phrasings_when_composio_search_misses(client_a, ctx_factory, monkeypatch):
    """Composio's own search returns nothing for "recent emails"; the local ranking still finds the mail list."""
    from types import SimpleNamespace as NS

    ctx = ctx_factory(client_a, "operator")
    raw = [NS(slug=s, name=n, description=d, tags=["readOnlyHint"], toolkit=NS(slug="outlook"), input_parameters={},
              is_deprecated=False)
           for s, n, d in [("OUTLOOK_LIST_MESSAGES", "List messages", "List email messages in the mailbox"),
                           ("OUTLOOK_GET_SCHEDULE", "Get schedule", "Free/busy times"),
                           ("OUTLOOK_LIST_CONTACTS", "List contacts", "Address book")]]
    monkeypatch.setattr(acts, "connected_toolkits", lambda c: ["outlook"])
    monkeypatch.setattr(acts, "_TOOLKIT_ACTIONS", {})
    monkeypatch.setattr(composio, "get_client", lambda: NS(tools=NS(
        get_raw_composio_tools=lambda search=None, **k: [] if search else raw)))
    with tenant_scope(ctx):
        found = acts.find(ctx, "any recent emails on outlook")
    assert found["actions"][0]["action_id"] == "OUTLOOK_LIST_MESSAGES"
