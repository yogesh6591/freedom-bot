"""Pack-only tools are enforced by the policy check itself, not only by the tool surface.

finance_lookup_policy, legal_find_clause, brand_get_voice and strategy_list_priorities belong to their
own packs only. Called under the general domain they are denied, whatever caller asks; under their own
pack they follow the pack's enablement.
"""

from __future__ import annotations

import dataclasses

import pytest

from bizos.agents.grounding import blocked_capabilities
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.tenancy.context import tenant_scope
from bizos.types import PolicyEffect

PACK_ONLY = {
    "finance_lookup_policy": "finance",
    "legal_find_clause": "legal",
    "brand_get_voice": "brand",
    "strategy_list_priorities": "strategy",
}
ALL_PACKS = ["general", "operations", "sales", "intake", "planning", "strategy", "finance", "brand", "legal"]


def _settings(client, enabled) -> ClientSettings:
    base = control.get_client(client.id).settings
    return ClientSettings.from_dict({**base.to_dict(), "enabled_domains": enabled, "purchased_domains": ALL_PACKS})


def _ctx_with_every_area(client, ctx_factory):
    ctx = ctx_factory(client, "operator")
    return dataclasses.replace(ctx, data_scopes=frozenset({"finance", "legal", "hr", "exec", "salary", "ops"}))


def _decide(ctx, settings, tool, domain):
    with tenant_scope(ctx):
        return evaluate(PolicyRequest(ctx=ctx, settings=settings, tool=tool, domain=domain))


@pytest.mark.parametrize("tool,pack", PACK_ONLY.items())
def test_pack_only_tools_are_denied_under_the_general_domain(client_scratch, ctx_factory, tool, pack):
    ctx = _ctx_with_every_area(client_scratch, ctx_factory)
    for enabled in (ALL_PACKS, [p for p in ALL_PACKS if p != pack]):
        decision = _decide(ctx, _settings(client_scratch, enabled), tool, "general")
        assert decision.effect == PolicyEffect.DENY and decision.rule == "TOOL_OUTSIDE_DOMAIN", (tool, enabled)


@pytest.mark.parametrize("tool,pack", PACK_ONLY.items())
def test_pack_only_tools_follow_their_packs_enablement(client_scratch, ctx_factory, tool, pack):
    ctx = _ctx_with_every_area(client_scratch, ctx_factory)
    on = _decide(ctx, _settings(client_scratch, ALL_PACKS), tool, pack)
    off = _decide(ctx, _settings(client_scratch, [p for p in ALL_PACKS if p != pack]), tool, pack)
    assert on.effect == PolicyEffect.ALLOW, (tool, on.rule, on.reason)
    assert off.effect == PolicyEffect.DENY and off.rule == "DOMAIN_DISABLED", (tool, off.rule)


def test_blocked_capabilities_only_name_capabilities_whose_pack_is_on(client_scratch, ctx_factory):
    viewer = ctx_factory(client_scratch, "viewer")  # no restricted data areas
    packs_off = [p for p in ALL_PACKS if p not in ("finance", "legal")]
    off = "\n".join(blocked_capabilities(viewer, packs_off))
    on = "\n".join(blocked_capabilities(viewer, ALL_PACKS))
    assert "Look up finance policy" not in off and "Find a recorded clause summary" not in off
    assert "Look up finance policy" in on and "Find a recorded clause summary" in on
