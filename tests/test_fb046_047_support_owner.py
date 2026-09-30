"""FB-046 support routing + FB-047 owner portfolio."""

from __future__ import annotations

from bizos.control import store as control
from bizos.owner import ensure_portfolio_defaults, owner_portfolio
from bizos.support import (
    ASSIGNEE_CEPOCH,
    ASSIGNEE_JEANNE,
    ESCALATE_CEO,
    create_ticket,
    escalate_to_jeanne,
    list_tickets,
    return_to_cepoch,
    set_status,
)


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


def test_owner_portfolio_includes_bought_risk_needs_revenue(client_a):
    ensure_portfolio_defaults(client_id=client_a.id, actor="pytest")
    ticket = create_ticket(
        client_id=client_a.id,
        subject="Needs Jeanne",
        created_by="pytest",
    )
    escalate_to_jeanne(ticket_id=ticket["id"], reason="RELATIONSHIP_RISK", by="pytest")

    portfolio = owner_portfolio()
    assert portfolio["client_count"] >= 1
    assert portfolio["needs_me_count"] >= 1
    row = next(c for c in portfolio["clients"] if c["client_id"] == client_a.id)
    assert "general" in row["purchased_domains"] or len(row["purchased_domains"]) >= 1
    assert row["implementation_status"] in {"setup", "live", "blocked"}
    assert row["needs_me_count"] >= 1
    # An unpriced client shows no revenue rather than a demo figure (M01-20).
    assert "customer_price_usd" in row["revenue"]
    # Refresh client after portfolio defaults write
    assert control.get_client(client_a.id).id == client_a.id
