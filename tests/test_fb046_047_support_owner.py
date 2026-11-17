"""FB-046 support routing + FB-047 owner portfolio."""

from __future__ import annotations

from freedombot.control import store as control
from freedombot.owner import derive_risk, derive_status, owner_portfolio, save_portfolio
from freedombot.support import (
    ASSIGNEE_CEPOCH,
    ASSIGNEE_JEANNE,
    ESCALATE_CEO,
    create_ticket,
    escalate_to_jeanne,
    list_tickets,
    return_to_cepoch,
    set_status,
)


def test_chat_tool_raises_a_cepoch_ticket_without_approval(client_a, scope_factory):
    from dataclasses import replace

    from freedombot.tenancy.context import tenant_scope
    from freedombot.tools.base import guarded_call
    from freedombot.types import PolicyEffect

    # Employees (VIEWER) raise tickets too, and WAIT_FOR_APPROVAL does not queue them.
    scope = replace(scope_factory(client_a, "viewer", domain="general"), session_id="ses_fb046_chat")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "support_create_ticket",
            {"subject": "Chat page shows a blank screen after login", "body": "Since this morning.", "severity": "HIGH"},
        )
        assert outcome.ok and outcome.effect == PolicyEffect.ALLOW, outcome.message
        ticket_id = outcome.data["id"]
        mine = guarded_call(scope, "support_my_tickets", {})
    ticket = next(t for t in list_tickets(client_id=client_a.id) if t["id"] == ticket_id)
    assert ticket["assignee"] == ASSIGNEE_CEPOCH and ticket["severity"] == "HIGH"
    assert ticket["created_by"] == scope.ctx.user_id
    assert "ses_fb046_chat" in ticket["body"]
    assert any(t["id"] == ticket_id for t in mine.data)


def test_chat_ticket_with_same_subject_merges_and_others_are_not_listed(client_a, scope_factory):
    from freedombot.tenancy.context import tenant_scope
    from freedombot.tools.base import guarded_call

    viewer = scope_factory(client_a, "viewer", domain="general")
    operator = scope_factory(client_a, "operator", domain="general")
    args = {"subject": "Export to CSV button does nothing", "body": "Tried twice."}
    with tenant_scope(viewer.ctx):
        first = guarded_call(viewer, "support_create_ticket", args)
        second = guarded_call(viewer, "support_create_ticket", {**args, "body": "Still broken."})
    assert first.data["id"] == second.data["id"]
    with tenant_scope(operator.ctx):
        theirs = guarded_call(operator, "support_my_tickets", {})
    assert all(t["id"] != first.data["id"] for t in (theirs.data or []))


def test_new_ticket_routes_to_cepoch_not_jeanne(client_a):
    ticket = create_ticket(
        client_id=client_a.id,
        subject="How do we reset a user password?",
        body="Routine ops question",
        created_by="pytest",
    )
    assert ticket["assignee"] == ASSIGNEE_CEPOCH
    assert ticket["status"] == "OPEN"
    assert ticket["history"][0]["event"] == "created"


def test_escalate_requires_allowed_reason(client_a):
    ticket = create_ticket(
        client_id=client_a.id,
        subject="Needs judgment",
        created_by="pytest",
    )
    try:
        escalate_to_jeanne(ticket_id=ticket["id"], reason="SALES_HANDOFF", by="pytest")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "CEO_JUDGMENT" in str(exc)


def test_escalate_to_jeanne_and_return(client_a):
    ticket = create_ticket(
        client_id=client_a.id,
        subject="At-risk renewal",
        severity="CRITICAL",
        created_by="pytest",
    )
    escalated = escalate_to_jeanne(
        ticket_id=ticket["id"],
        reason=ESCALATE_CEO,
        by="pytest",
        note="CEO asked for call",
    )
    assert escalated["assignee"] == ASSIGNEE_JEANNE
    assert escalated["status"] == "ESCALATED"
    assert escalated["escalate_reason"] == ESCALATE_CEO
    needs = list_tickets(client_id=client_a.id, needs_jeanne=True)
    assert any(t["id"] == ticket["id"] for t in needs)

    back = return_to_cepoch(ticket_id=ticket["id"], by="pytest")
    assert back["assignee"] == ASSIGNEE_CEPOCH
    assert back["status"] == "IN_PROGRESS"


def test_resolve_returns_to_cepoch(client_a):
    ticket = create_ticket(client_id=client_a.id, subject="Done item", created_by="pytest")
    escalate_to_jeanne(ticket_id=ticket["id"], reason="AT_RISK", by="pytest")
    done = set_status(ticket_id=ticket["id"], status="RESOLVED", by="pytest")
    assert done["status"] == "RESOLVED"
    assert done["assignee"] == ASSIGNEE_CEPOCH


def _with_onboarding(client, **onboarding):
    from dataclasses import replace

    from freedombot.control.models import ClientSettings

    payload = client.settings.to_dict()
    payload["onboarding"] = onboarding
    return replace(client, settings=ClientSettings.from_dict(payload))


def _ticket(subject, *, severity="NORMAL", assignee=ASSIGNEE_CEPOCH, reason=None, status="OPEN"):
    return {
        "id": subject,
        "subject": subject,
        "severity": severity,
        "assignee": assignee,
        "escalate_reason": reason,
        "status": status,
        "history": [],
    }


def test_owner_status_follows_handoff_launch_and_critical_tickets(client_a):
    from freedombot.util.timeutil import utcnow

    now = utcnow().isoformat()
    assert derive_status(_with_onboarding(client_a), [])["value"] == "not_started"
    setup = _with_onboarding(client_a, handoff_at=now)
    status = derive_status(setup, [])
    assert status["value"] == "setup" and "pending" in status["reasons"][0]
    live = _with_onboarding(client_a, handoff_at=now, launch={"signed_off_at": now})
    assert derive_status(live, [])["value"] == "live"
    blocked = derive_status(live, [_ticket("Login broken for everyone", severity="CRITICAL")])
    assert blocked["value"] == "blocked" and "Login broken" in blocked["reasons"][0]
    # A resolved critical ticket no longer blocks.
    done = _ticket("Old outage", severity="CRITICAL", status="RESOLVED")
    assert derive_status(live, [done])["value"] == "live"
    judgment = _ticket("Decide MSA risk", severity="CRITICAL", assignee=ASSIGNEE_JEANNE, reason="CEO_JUDGMENT", status="ESCALATED")
    assert derive_status(live, [judgment])["value"] == "live"


def test_owner_risk_comes_from_escalations_tickets_and_stalled_setup(client_a):
    from datetime import timedelta

    from freedombot.util.timeutil import utcnow

    now = utcnow()
    fresh = _with_onboarding(client_a, handoff_at=now.isoformat())
    assert derive_risk(fresh, [], "setup", now) == {"value": "healthy", "reasons": []}

    at_risk = derive_risk(
        fresh,
        [_ticket("Renewal wobbling", assignee=ASSIGNEE_JEANNE, reason="AT_RISK", status="ESCALATED")],
        "setup",
        now,
    )
    assert at_risk["value"] == "at_risk" and "Renewal wobbling" in at_risk["reasons"][0]

    two_high = [_ticket("Slow page", severity="HIGH"), _ticket("Export fails", severity="HIGH")]
    assert derive_risk(fresh, two_high, "setup", now)["value"] == "watch"

    stalled = _with_onboarding(client_a, handoff_at=(now - timedelta(days=45)).isoformat())
    risk = derive_risk(stalled, [], "setup", now)
    assert risk["value"] == "watch" and "45 days" in risk["reasons"][0]


def test_owner_risk_flags_a_live_client_nobody_uses(client_a):
    from datetime import timedelta

    from freedombot.util.timeutil import utcnow

    live = _with_onboarding(client_a, launch={"signed_off_at": utcnow().isoformat()})
    far_future = utcnow() + timedelta(days=60)
    risk = derive_risk(live, [], "live", far_future)
    assert risk["value"] == "watch"
    assert "sign-in" in risk["reasons"][0] or "signed in" in risk["reasons"][0]


def test_owner_portfolio_includes_bought_risk_needs_revenue_and_history(client_a):
    ticket = create_ticket(
        client_id=client_a.id,
        subject="Needs Jeanne",
        created_by="pytest",
    )
    escalate_to_jeanne(ticket_id=ticket["id"], reason="RELATIONSHIP_RISK", by="pytest")

    portfolio = owner_portfolio()
    assert portfolio["client_count"] >= 1
    row = next(c for c in portfolio["clients"] if c["client_id"] == client_a.id)
    assert row["purchased_domains"]
    assert row["implementation_status"] in {"not_started", "setup", "live", "blocked"}
    assert row["implementation"]["source"] == "derived" and row["implementation"]["reasons"]
    assert row["account_risk"] == "at_risk"
    assert any("Needs Jeanne" in r for r in row["risk"]["reasons"])
    assert any(i["kind"] == "escalation" and i["id"] == ticket["id"] for i in row["needs_me"])
    assert any("Needs Jeanne" in h["text"] for h in row["history"])
    assert any(q["client_id"] == client_a.id for q in portfolio["needs_me_queue"])
    # An unpriced client shows no revenue and asks Jeanne to set it, not a demo figure.
    assert "customer_price_usd" in row["revenue"]
    if row["revenue"]["customer_price_usd"] is None:
        assert any(i["kind"] == "pricing" for i in row["needs_me"])
    return_to_cepoch(ticket_id=ticket["id"], by="pytest")
    set_status(ticket_id=ticket["id"], status="RESOLVED", by="pytest")


def test_owner_override_is_labelled_and_can_be_cleared(client_a):
    save_portfolio(
        client_id=client_a.id,
        implementation_status="blocked",
        account_risk="watch",
        owner_notes="Waiting on their IT for SSO",
        ctx_user_id="jeanne",
    )
    row = next(c for c in owner_portfolio()["clients"] if c["client_id"] == client_a.id)
    assert row["implementation_status"] == "blocked" and row["implementation"]["source"] == "owner"
    assert row["account_risk"] == "watch" and row["risk"]["source"] == "owner"
    assert row["owner_notes"] == "Waiting on their IT for SSO"
    assert any("Waiting on their IT" in h["text"] for h in row["history"])

    save_portfolio(
        client_id=client_a.id,
        implementation_status=None,
        account_risk="",
        owner_notes="",
        ctx_user_id="jeanne",
    )
    row = next(c for c in owner_portfolio()["clients"] if c["client_id"] == client_a.id)
    assert row["implementation"]["source"] == "derived"
    assert row["risk"]["source"] == "derived"
    assert row["implementation_status"] == row["implementation"]["derived"]

    try:
        save_portfolio(client_id=client_a.id, implementation_status="done", account_risk=None, owner_notes="", ctx_user_id="jeanne")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "implementation_status" in str(exc)
