"""
Jeanne exception-only owner view (FB-047)
=========================================

Thin portfolio for JeanneCAIO: client count, what each bought, implementation
status, risk, what needs me, recent history, and revenue from her side.
Not a CRM — no pipeline, leads, or campaigns.
"""

from __future__ import annotations

from typing import Any

from bizos.control import store as control
from bizos.control.models import Client
from bizos.pricing import get_rules
from bizos.support import list_tickets


def _portfolio_blob(client: Client) -> dict[str, Any]:
    onboarding = dict(client.settings.onboarding or {})
    return dict(onboarding.get("portfolio") or {})


def ensure_portfolio_defaults(*, client_id: str, actor: str = "bootstrap") -> dict[str, Any]:
    """Face portfolio fields for owner view demos until Jeanne confirms live values."""
    client = control.get_client(client_id)
    onboarding = dict(client.settings.onboarding or {})
    portfolio = dict(onboarding.get("portfolio") or {})
    changed = False
    defaults = {
        "implementation_status": "live",
        "account_risk": "healthy",
        "owner_notes": "Face portfolio row for FB-047 demos.",
    }
    for key, value in defaults.items():
        if key not in portfolio:
            portfolio[key] = value
            changed = True
    if not changed:
        return portfolio
    onboarding["portfolio"] = portfolio
    payload = client.settings.to_dict()
    payload["onboarding"] = onboarding
    from bizos.control.models import ClientSettings

    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=actor
    )
    return portfolio


def save_portfolio(
    *,
    client_id: str,
    implementation_status: str,
    account_risk: str,
    owner_notes: str,
    ctx_user_id: str,
) -> dict[str, Any]:
    status = (implementation_status or "setup").strip().casefold()
    if status not in {"setup", "live", "blocked"}:
        raise ValueError("implementation_status must be setup, live, or blocked")
    risk = (account_risk or "healthy").strip().casefold()
    if risk not in {"healthy", "watch", "at_risk"}:
        raise ValueError("account_risk must be healthy, watch, or at_risk")

    client = control.get_client(client_id)
    onboarding = dict(client.settings.onboarding or {})
    portfolio = {
        **dict(onboarding.get("portfolio") or {}),
        "implementation_status": status,
        "account_risk": risk,
        "owner_notes": (owner_notes or "")[:2000],
    }
    onboarding["portfolio"] = portfolio
    payload = client.settings.to_dict()
    payload["onboarding"] = onboarding
    from bizos.control.models import ClientSettings

    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx_user_id
    )
    return portfolio


def _client_row(client: Client) -> dict[str, Any]:
    portfolio = _portfolio_blob(client)
    rules = get_rules(client.settings)
    needs = list_tickets(client_id=client.id, needs_jeanne=True)
    recent = list_tickets(client_id=client.id)[:5]
    return {
        "client_id": client.id,
        "slug": client.slug,
        "company_name": client.company_name,
        "status": client.status,
        "purchased_domains": list(client.settings.purchased_domains or []),
        "enabled_domains": list(client.settings.enabled_domains or []),
        "implementation_status": portfolio.get("implementation_status") or "setup",
        "account_risk": portfolio.get("account_risk") or "healthy",
        "owner_notes": portfolio.get("owner_notes") or "",
        "needs_me": [
            {
                "id": t["id"],
                "subject": t["subject"],
                "escalate_reason": t.get("escalate_reason"),
                "severity": t["severity"],
                "updated_at": t.get("updated_at"),
            }
            for t in needs
        ],
        "needs_me_count": len(needs),
        "recent_history": [
            {
                "id": t["id"],
                "subject": t["subject"],
                "status": t["status"],
                "assignee": t["assignee"],
                "updated_at": t.get("updated_at"),
            }
            for t in recent
        ],
        "revenue": {
            "placeholder": bool(rules.get("placeholder", True)),
            "customer_price_usd": rules.get("customer_price_usd"),
            "margin_percent": rules.get("margin_percent"),
            "quote_status": rules.get("quote_status"),
            "disclaimer": rules.get("disclaimer"),
        },
    }


def owner_portfolio() -> dict[str, Any]:
    """Aggregate exception-only owner view across clients (control metadata only)."""
    clients = [c for c in control.list_clients() if c.status == "ACTIVE"]
    rows = [_client_row(c) for c in clients]
    needs_total = sum(r["needs_me_count"] for r in rows)
    at_risk = sum(1 for r in rows if r["account_risk"] == "at_risk")
    return {
        "placeholder": True,
        "disclaimer": (
            "FB-047 JeanneCAIO exception-only owner view — not a CRM. "
            "Routine support stays with Cepoch (FB-046)."
        ),
        "client_count": len(rows),
        "needs_me_count": needs_total,
        "at_risk_count": at_risk,
        "clients": rows,
        "needs_me_queue": list_tickets(needs_jeanne=True)[:20],
    }
