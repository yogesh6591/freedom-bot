"""v2 routing and tool loading: the menu, bundles and load_tools never widen what a user can see."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from app.assistant.assistant import _packs_for
from bizos.agents.merged_tools import MERGED, WRAPPED
from bizos.agents.router_agent import FALLBACK_ROUTE, route_and_prefetch, route_query
from bizos.agents.routing import (
    ALWAYS_ON, BUNDLE_EXTRAS, DOMAIN_DESCRIPTIONS, LOAD_TOOLS, MODEL_TOOLS, Route, ToolLoader, bundle,
    clean_route, domain_menu, home_tools, render_router_prompt, tools_for_route, visible_tools,
)
from bizos.agents.surface import build_unified_tools
from bizos.agents.tool_meta import DOMAINS, TOOL_META
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.tenancy.context import tenant_scope
from bizos.tools import base as tools_base
from bizos.tools.base import RunScope
from tests.test_unified_assistant import SCRIPT, _reset, fake_model  # noqa: F401

ROLES = ["viewer", "drafter", "approver", "operator", "admin"]
ALL_PACKS = ["general", "operations", "sales", "intake", "planning", "strategy", "finance", "brand", "legal"]


def _settings(client, **overrides) -> ClientSettings:
    return ClientSettings.from_dict({**control.get_client(client.id).settings.to_dict(), **overrides})


def _visible(client, ctx, settings=None, *, campaign_allowed=True):
    settings = settings or _settings(client)
    scope = RunScope(ctx=ctx, settings=settings, domain="general")
    packs = _packs_for(settings, "")
    with tenant_scope(ctx):
        return visible_tools(scope, packs, campaign_allowed=campaign_allowed), scope, packs


# --------------------------------------------------------------------------- bundles


def test_every_model_tool_has_one_home_domain_and_bundles_match_the_architecture():
    assert sorted(t for d in DOMAINS for t in home_tools(d)) == sorted(MODEL_TOOLS)
    table = (Path(__file__).parents[1] / "docs/agent/ARCHITECTURE.md").read_text().split("## 7. Routing domains")[1]
    for line in table.split("## 8.")[0].splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("Domain", "---") or cells[0].startswith("-"):
            continue
        documented = re.findall(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", cells[2])
        assert sorted(documented) == sorted(bundle(cells[0])), cells[0]
        assert DOMAIN_DESCRIPTIONS[cells[0]] == cells[1], cells[0]
    assert set(BUNDLE_EXTRAS) <= set(DOMAINS)


# --------------------------------------------------------------------------- visibility


@pytest.mark.parametrize("who", ROLES)
def test_v2_visibility_equals_the_current_assistant_surface(client_scratch, ctx_factory, who):
    """Routing never changes who may see a tool: same set as today's surface, plus merged tools."""
    ctx = ctx_factory(client_scratch, who)
    visible, scope, packs = _visible(client_scratch, ctx)
    with tenant_scope(ctx):
        current = {getattr(t, "name", None) for t in build_unified_tools(scope, packs)[0]}
    registered = {t for t in visible if t not in MERGED}
    campaign = {t for t in registered if TOOL_META[t].domain == "campaign"}
    assert registered - campaign == current - WRAPPED


@pytest.mark.parametrize("who", ["viewer", "drafter", "approver", "operator"])
def test_non_admins_never_see_admin_or_campaign_domains(client_scratch, ctx_factory, who):
    visible, _, _ = _visible(client_scratch, ctx_factory(client_scratch, who))
    menu = domain_menu(visible)
    assert not [d for d in menu if d.startswith("admin_") or d == "campaign"], menu
    assert not [t for t in visible if t.startswith("admin_") or TOOL_META[t].domain == "campaign"]


def test_admins_see_admin_domains_and_campaign_only_where_allowed(client_scratch, ctx_factory):
    admin = ctx_factory(client_scratch, "admin")
    visible, _, _ = _visible(client_scratch, admin)
    menu = domain_menu(visible)
    assert {"admin_users", "admin_permissions", "campaign"} <= set(menu)
    no_campaign, _, _ = _visible(client_scratch, admin, campaign_allowed=False)
    assert "campaign" not in domain_menu(no_campaign)


@pytest.mark.parametrize("who", ROLES)
def test_tools_the_policy_always_denies_are_hidden_and_cannot_be_loaded(client_scratch, ctx_factory, who):
    settings = _settings(client_scratch, enabled_domains=ALL_PACKS, purchased_domains=ALL_PACKS)
    visible, _, _ = _visible(client_scratch, ctx_factory(client_scratch, who), settings)
    # Their only packs (strategy, legal) are capped at ADVISE, so the policy denies them for every role.
    assert not {"strategy_record_option", "legal_flag_for_counsel"} & visible
    loader = ToolLoader.for_route(Route(domains=["ops"], kind="task"), visible)
    loader.load_tools("ops")
    assert not {"strategy_record_option", "legal_flag_for_counsel"} & set(loader.loaded)


def test_disabled_packs_hide_their_tools(client_scratch, ctx_factory):
    operator = ctx_factory(client_scratch, "operator")
    with_finance = _settings(client_scratch, enabled_domains=ALL_PACKS, purchased_domains=ALL_PACKS)
    without = _settings(client_scratch, enabled_domains=[p for p in ALL_PACKS if p != "finance"],
                        purchased_domains=ALL_PACKS)
    assert "finance_draft_invoice" in _visible(client_scratch, operator, with_finance)[0]
    hidden, _, _ = _visible(client_scratch, operator, without)
    assert not {"finance_draft_invoice", "finance_propose_adjustment", "finance_lookup_policy"} & hidden
    assert "finance_list_invoices" in hidden  # also in the operations pack, which is still on


def test_merged_tools_follow_the_old_tools_they_wrap(client_scratch, ctx_factory):
    drafter, _, _ = _visible(client_scratch, ctx_factory(client_scratch, "drafter"))
    approver, _, _ = _visible(client_scratch, ctx_factory(client_scratch, "approver"))
    assert "memory_add" in drafter and "approvals_decide" not in drafter
    assert "approvals_decide" in approver
    assert not WRAPPED & (drafter | approver)


# --------------------------------------------------------------------------- loading


def test_a_route_loads_its_bundles_intersected_with_visibility(client_scratch, ctx_factory):
    visible, _, _ = _visible(client_scratch, ctx_factory(client_scratch, "drafter"))
    loaded = tools_for_route(["crm", "email"], visible)
    assert loaded[:2] == list(ALWAYS_ON)
    assert set(loaded) - {LOAD_TOOLS} <= visible
    assert "crm_delete_record" not in loaded  # in the crm bundle, but denied to drafters
    assert {"crm_search_deals", "email_send_message"} <= set(loaded)
    assert "calendar_create_event" not in loaded  # not routed
    # brand_get_voice is in the email bundle but only in the brand pack, which is off by default.
    assert "brand_get_voice" not in loaded
    with_brand = _settings(client_scratch, enabled_domains=ALL_PACKS, purchased_domains=ALL_PACKS)
    visible, _, _ = _visible(client_scratch, ctx_factory(client_scratch, "drafter"), with_brand)
    assert "brand_get_voice" in tools_for_route(["email"], visible)


def test_load_tools_adds_only_visible_tools_and_logs_each_miss(client_scratch, ctx_factory):
    visible, _, _ = _visible(client_scratch, ctx_factory(client_scratch, "viewer"))
    loader = ToolLoader.for_route(Route(domains=["crm"], kind="question"), visible)
    added, message = loader.load_tools("admin_users")
    assert added == [] and "not available" in message
    added, _ = loader.load_tools("calendar")
    assert added and set(added) <= visible
    _, message = loader.load_tools("nonsense")
    assert "no 'nonsense' domain" in message
    assert [m["domain"] for m in loader.misses] == ["admin_users", "calendar", "nonsense"]


def test_a_route_to_a_domain_not_on_the_menu_loads_nothing_from_it(client_scratch, ctx_factory):
    visible, _, _ = _visible(client_scratch, ctx_factory(client_scratch, "drafter"))
    loader = ToolLoader.for_route(Route(domains=["admin_users", "crm"], kind="task"), visible)
    assert not [t for t in loader.loaded if t.startswith("admin_")]
    assert clean_route(Route(domains=["admin_users", "crm"], kind="task"), domain_menu(visible)).domains == ["crm"]


# --------------------------------------------------------------------------- router prompt and model


def test_router_prompt_lists_only_the_menu_and_keeps_its_examples():
    prompt = render_router_prompt(user_name="Meena", user_roles=["DRAFTER"], today="2026-10-21",
                                  timezone="Asia/Kolkata", recent_messages="", menu=["crm", "email"])
    assert "Meena" in prompt and "DRAFTER" in prompt and "Asia/Kolkata" in prompt
    assert "- crm: " in prompt and "- email: " in prompt and "- admin_users: " not in prompt
    assert '{"domains":["admin_users"]' in prompt  # JSON examples are not mangled
    assert not re.search(r"\{(user_name|user_roles|today|timezone|recent_messages|available_domains)\}", prompt)


def test_route_query_returns_the_models_structured_route(fake_model):
    SCRIPT[:] = [{"content": json.dumps({"domains": ["crm", "email"], "kind": "task",
                                          "entities": {"contact": "Raj"}, "clarify": None})}]
    route = route_query("route this", "Move the Acme deal to won and email Raj")
    assert route.domains == ["crm", "email"] and route.entities == {"contact": "Raj"}


def test_a_router_failure_routes_nowhere_instead_of_failing(fake_model):
    SCRIPT[:] = [{"status": 500, "message": "boom"}] * 3
    assert route_query("route this", "hello") == FALLBACK_ROUTE


def test_memory_prefetch_runs_in_parallel_with_routing(client_scratch, ctx_factory, monkeypatch):
    started = threading.Event()
    real = tools_base.guarded_call

    def spy(scope, tool, arguments=None):
        started.set()
        return real(scope, tool, arguments)

    monkeypatch.setattr("bizos.agents.router_agent.guarded_call", spy)

    def slow_route():
        # Only finishes if memory_search started while routing was still running.
        assert started.wait(timeout=10), "memory_search did not start in parallel"
        return Route(domains=["memory"], kind="question")

    ctx = ctx_factory(client_scratch, "drafter")
    scope = RunScope(ctx=ctx, settings=_settings(client_scratch), domain="general")
    with tenant_scope(ctx):
        out = route_and_prefetch(slow_route, scope, "refund policy")
    assert out.route.domains == ["memory"] and out.memory is not None and out.memory.ok
