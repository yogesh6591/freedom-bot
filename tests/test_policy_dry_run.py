"""The policy dry-run matches the real policy check, and has no side effects.

For every registered tool, every role and every execution mode, the gate the dry-run predicts
must equal what guarded_call actually does. Effects and the approval queue are stubbed, so the
real decision path runs (risk inputs, classifier, policy rules, preconditions, not-queueable)
without changing anything.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.assistant.assistant import _packs_for
from bizos.actions import store as actions
from bizos.audit import events as audit
from bizos.connectors.base import ConnectorResult
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.rbac.registry import TOOL_SPECS
from bizos.tenancy.context import tenant_scope
from bizos.tools import base as tools_base
from bizos.tools.base import RunScope, guarded_call
from bizos.tools.dry_run import GATE_FOR_EFFECT, bound_scope, dry_run, dry_run_for_user
from bizos.types import ExecutionMode

ROLES = ("viewer", "drafter", "approver", "operator", "admin")
MODES = tuple(ExecutionMode)

#: Arguments that exercise risk inputs, recipient rules and preconditions.
SAMPLE_ARGS = {
    "email_send_message": {"recipients": ["raj@acme.example.com"], "subject": "s", "body": "b"},
    "email_create_draft": {"recipients": ["raj@acme.example.com"], "subject": "s", "body": "b"},
    "calendar_create_event": {"title": "t", "starts_at": "2026-11-02T10:00:00Z", "ends_at": "2026-11-02T10:30:00Z",
                              "attendees": ["raj@acme.example.com"]},
    "crm_bulk_update_contacts": {"field": "owner", "value": "Meena", "lifecycle_filter": "lead"},
    "crm_update_contact_field": {"contact_id": "c_1", "field": "owner", "value": "Meena"},
    "crm_update_deal_stage": {"deal_id": "d_1", "stage": "won"},
    "crm_tag_lead": {"contact_id": "c_1", "add_tags": ["vip"], "lifecycle": "customer"},
    "finance_draft_invoice": {"customer": "Acme"},
    "finance_propose_adjustment": {"customer": "Acme", "amount": 50, "reason": "goodwill"},
}


def _settings(client, mode: ExecutionMode) -> ClientSettings:
    base = control.get_client(client.id).settings
    return ClientSettings.from_dict({**base.to_dict(), "default_execution_mode": mode.value})


@pytest.fixture
def stubbed(monkeypatch):
    """Real decision path; no effect runs and nothing is queued."""
    ran: list[str] = []

    def stub(name):
        def effect(scope, args):
            ran.append(name)
            return ConnectorResult(ok=True, summary=f"stub {name}")
        return effect

    for name in list(tools_base.EFFECTS):
        monkeypatch.setitem(tools_base.EFFECTS, name, stub(name))
    monkeypatch.setattr(tools_base.action_store, "create_action", lambda **kw: SimpleNamespace(id="act_stub"))
    return ran


@pytest.mark.parametrize("mode", MODES, ids=str)
@pytest.mark.parametrize("who", ROLES)
def test_dry_run_matches_the_real_policy_check(client_scratch, ctx_factory, stubbed, who, mode):
    ctx = ctx_factory(client_scratch, who)
    settings = _settings(client_scratch, mode)
    scope = RunScope(ctx=ctx, settings=settings, domain="general")
    packs = _packs_for(settings, "")
    mismatches = []
    with tenant_scope(ctx):
        for spec in TOOL_SPECS:
            for args in ({}, SAMPLE_ARGS.get(spec.name)):
                if args is None:
                    continue
                bound = bound_scope(scope, spec.name, packs)
                predicted = dry_run(bound, spec.name, args)
                actual = guarded_call(bound, spec.name, args)
                if GATE_FOR_EFFECT[actual.effect] != predicted.gate:
                    mismatches.append((spec.name, args, predicted.gate, str(actual.effect), actual.message))
                assert predicted == dry_run_for_user(scope, spec.name, args, packs=packs)
    assert not mismatches, mismatches


def test_dry_run_writes_nothing(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "operator")
    settings = _settings(client_scratch, ExecutionMode.WAIT_FOR_APPROVAL)
    scope = RunScope(ctx=ctx, settings=settings, domain="general")
    packs = _packs_for(settings, "")
    with tenant_scope(ctx):
        audits, queued = audit.count(ctx), {a.id for a in actions.list_actions(limit=500, ctx=ctx)}
        for spec in TOOL_SPECS:
            dry_run_for_user(scope, spec.name, SAMPLE_ARGS.get(spec.name) or {}, packs=packs)
        assert audit.count(ctx) == audits
        assert {a.id for a in actions.list_actions(limit=500, ctx=ctx)} == queued


def test_dry_run_reports_the_binding_rule(client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "drafter")
    settings = _settings(client_scratch, ExecutionMode.WAIT_FOR_APPROVAL)
    scope = RunScope(ctx=ctx, settings=settings, domain="general")
    with tenant_scope(ctx):
        send = dry_run_for_user(scope, "email_send_message", SAMPLE_ARGS["email_send_message"],
                                packs=_packs_for(settings, ""))
        delete = dry_run_for_user(scope, "crm_delete_record", {}, packs=_packs_for(settings, ""))
    assert (send.gate, send.rule) == ("draft", "RBAC_DRAFT_ONLY")
    assert delete.gate == "deny"
