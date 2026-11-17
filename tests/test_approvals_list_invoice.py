"""approvals_list must work while an invoice draft is waiting for approval.

Listing a pending finance_draft_invoice adds the customer and amount from the pending billables. That lookup
used to import a module that no longer exists, so the whole list failed whenever an invoice was pending.
"""

from __future__ import annotations

from freedombot.actions import store as actions
from freedombot.policy.engine import evaluate
from freedombot.policy.models import PolicyRequest
from freedombot.tenancy.context import tenant_scope
from freedombot.tools.base import RunScope, guarded_call
from freedombot.types import ActionStatus


def test_approvals_list_shows_a_pending_invoice(client_scratch, ctx_factory):
    admin = ctx_factory(client_scratch, "admin")  # sees the finance area the invoice belongs to
    with tenant_scope(admin):
        # Only stored on the action; the scratch workspace has no finance pack to evaluate against.
        decision = evaluate(PolicyRequest(ctx=admin, settings=client_scratch.settings, tool="crm_create_note",
                                          domain="operations", connected_integrations=frozenset({"crm"})))
        queued = actions.create_action(
            title="Invoice for Wayne Retail", description="Invoice for Wayne Retail", explanation="test",
            tool="finance_draft_invoice", payload={"customer": "Wayne Retail"}, decision=decision,
            settings=client_scratch.settings, status=ActionStatus.PENDING_APPROVAL, domain="finance", ctx=admin,
        )

    with tenant_scope(admin):
        out = guarded_call(RunScope(ctx=admin, settings=client_scratch.settings, domain="operations"),
                           "approvals_list", {})
    assert out.ok, out.message
    listed = {a["action_id"]: a for a in out.data["actions"]}
    assert queued.id in listed
    assert listed[queued.id]["customer"] == "Wayne Retail"
