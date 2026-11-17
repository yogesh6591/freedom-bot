"""Email tools only take email addresses: a name such as "Raj" is refused before anything is queued or sent."""

from __future__ import annotations

import pytest

from freedombot.actions import store as actions
from freedombot.agents.tool_args import schema_for_tool
from freedombot.connectors.base import ConnectorResult
from freedombot.tenancy.context import tenant_scope
from freedombot.tools import base as tools_base
from freedombot.tools.base import RunScope, guarded_call


@pytest.fixture
def sent(monkeypatch):
    ran = []
    for name in ("email_send_message", "email_create_draft"):
        monkeypatch.setitem(tools_base.EFFECTS, name,
                            lambda scope, args, _n=name: ran.append((_n, args)) or ConnectorResult(ok=True, summary="ok"))
    return ran


def _call(client, ctx_factory, who, tool, args):
    ctx = ctx_factory(client, who)
    with tenant_scope(ctx):
        before = {a.id for a in actions.list_actions(limit=500, ctx=ctx)}
        out = guarded_call(RunScope(ctx=ctx, settings=client.settings, domain="sales"), tool, args)
        queued = [a for a in actions.list_actions(limit=500, ctx=ctx) if a.id not in before]
    return out, queued


@pytest.mark.parametrize("tool", ["email_send_message", "email_create_draft"])
@pytest.mark.parametrize("who", ["operator", "admin"])
def test_a_name_is_refused_with_a_lookup_hint_and_nothing_is_queued(client_scratch, ctx_factory, sent, tool, who):
    out, queued = _call(client_scratch, ctx_factory, who, tool,
                        {"recipients": ["Raj"], "subject": "Hi", "body": "Thanks"})
    assert not out.ok
    assert "'Raj' is not an email address" in out.message and "crm_search_contacts" in out.message
    assert queued == [] and sent == []


def test_a_name_in_cc_is_refused_too(client_scratch, ctx_factory, sent):
    out, queued = _call(client_scratch, ctx_factory, "operator", "email_send_message",
                        {"recipients": ["raj@acme.com"], "cc": ["Priya"], "subject": "Hi", "body": "Thanks"})
    assert not out.ok and "'Priya' is not an email address" in out.message and queued == []


def test_real_addresses_go_through_the_policy_check_as_before(client_scratch, ctx_factory, sent):
    out, queued = _call(client_scratch, ctx_factory, "operator", "email_send_message",
                        {"recipients": ["raj@acme.com"], "subject": "Hi", "body": "Thanks"})
    # The default workspace queues an operator's send for approval: the precheck did not get in the way.
    assert out.ok and len(queued) == 1 and queued[0].tool == "email_send_message"


def test_the_schema_tells_the_model_and_the_v2_guard_explains():
    from freedombot.agents.guard import Guard

    for tool in ("email_send_message", "email_create_draft"):
        items = schema_for_tool(tool)["properties"]["recipients"]["items"]
        assert items.get("pattern") and "never names" in schema_for_tool(tool)["properties"]["recipients"]["description"]
    guard = Guard(lambda tool: None)
    problem = guard.check_schema("email_send_message", {"recipients": ["Raj"], "subject": "s", "body": "b"})
    assert problem and "'Raj' is not an email address" in problem and "crm_search_contacts" in problem
    assert guard.check_schema("email_send_message", {"recipients": ["raj@acme.com"], "subject": "s", "body": "b"}) is None
