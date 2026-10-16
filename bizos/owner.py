"""
Jeanne exception-only owner view (FB-047)
=========================================

Thin portfolio for JeanneCAIO: client count, what each bought, implementation
status, risk, what needs me, history before a call, and revenue from her side.
Not a CRM — no pipeline, leads, or campaigns.

Status and risk are derived from each client's own record (sale handoff, setup,
launch checklist, support tickets, sign-ins), each with the reasons behind it.
Jeanne may override either; an override is labelled as hers and can be cleared.
Only control-plane metadata is read — never a client's business content.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from bizos.control import store as control
from bizos.control.models import Client, ClientSettings
from bizos.launch import checklist_summary
from bizos.onboarding import get_profile
from bizos.pricing import get_rules
from bizos.support import (
    ASSIGNEE_JEANNE,
    ESCALATE_AT_RISK,
    ESCALATE_RELATIONSHIP,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    STATUS_RESOLVED,
    list_tickets,
)
from bizos.util.timeutil import ensure_utc, utcnow

STATUSES = ("not_started", "setup", "live", "blocked")
RISKS = ("healthy", "watch", "at_risk")
#: Setup running longer than this without launch sign-off is a stalled implementation.
STALLED_SETUP_DAYS = 30
#: A live client nobody has signed in to for this long is drifting away.
INACTIVE_DAYS = 14
HISTORY_LIMIT = 10


def _portfolio_blob(client: Client) -> dict[str, Any]:
    return dict((client.settings.onboarding or {}).get("portfolio") or {})


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _open(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tickets if t["status"] != STATUS_RESOLVED]


def derive_status(client: Client, tickets: list[dict[str, Any]]) -> dict[str, Any]:
    onboarding = client.settings.onboarding or {}
    launch = dict(onboarding.get("launch") or {})
    # A judgment call escalated to Jeanne is in "needs me", not an implementation blocker.
    critical = [
        t
        for t in _open(tickets)
        if t["severity"] == SEVERITY_CRITICAL and t["assignee"] != ASSIGNEE_JEANNE
    ]
    if critical:
        return {
            "value": "blocked",
            "reasons": [f"Open critical support ticket: {critical[0]['subject']}"],
        }
    if launch.get("signed_off_at"):
        reasons = [f"Launch signed off {launch['signed_off_at'][:10]}"]
        if launch.get("conditional"):
            reasons.append("Conditional sign-off — checklist items were still pending")
        return {"value": "live", "reasons": reasons}
    summary = checklist_summary(client.settings)
    profile = get_profile(client.settings)
    if onboarding.get("handoff_at") or profile.configured_at:
        reasons = [f"Launch checklist: {summary['pending_count']} item(s) still pending"]
        if profile.inconsistencies:
            reasons.append(f"{len(profile.inconsistencies)} contradiction(s) in the assessment to resolve")
        return {"value": "setup", "reasons": reasons}
    return {"value": "not_started", "reasons": ["No sale handoff or setup recorded yet"]}


def derive_risk(
    client: Client, tickets: list[dict[str, Any]], status: str, now: datetime
) -> dict[str, Any]:
    reasons: list[tuple[str, str]] = []
    open_tickets = _open(tickets)
    for t in open_tickets:
        if t["assignee"] == ASSIGNEE_JEANNE and t.get("escalate_reason") in {
            ESCALATE_AT_RISK,
            ESCALATE_RELATIONSHIP,
        }:
            label = "At-risk account" if t["escalate_reason"] == ESCALATE_AT_RISK else "Relationship risk"
            reasons.append(("at_risk", f"{label} escalation: {t['subject']}"))
    critical = [t for t in open_tickets if t["severity"] == SEVERITY_CRITICAL]
    if critical:
        reasons.append(("at_risk", f"{len(critical)} open critical support ticket(s)"))
    high = [t for t in open_tickets if t["severity"] == SEVERITY_HIGH]
    if len(high) >= 2:
        reasons.append(("watch", f"{len(high)} open high-severity support tickets"))

    onboarding = client.settings.onboarding or {}
    started = _parse(onboarding.get("handoff_at") or get_profile(client.settings).configured_at)
    if status == "setup" and started and started < now - timedelta(days=STALLED_SETUP_DAYS):
        reasons.append(
            ("watch", f"In setup for {(now - started).days} days without launch sign-off")
        )

    if status == "live":
        logins = [ensure_utc(u.last_login_at) for u in control.list_users(client.id) if u.last_login_at]
        last = max(logins) if logins else None
        if last is None:
            reasons.append(("watch", "Live, but no client user has signed in yet"))
        elif last < now - timedelta(days=INACTIVE_DAYS):
            reasons.append(("watch", f"No client sign-in for {(now - last).days} days"))

    level = "healthy"
    if any(r == "at_risk" for r, _ in reasons):
        level = "at_risk"
    elif reasons:
        level = "watch"
    return {"value": level, "reasons": [text for _, text in reasons]}


def _with_override(derived: dict[str, Any], override: Optional[dict[str, Any]]) -> dict[str, Any]:
    out = {**derived, "derived": derived["value"], "source": "derived"}
    if override and override.get("value"):
        out.update(
            value=override["value"],
            source="owner",
            set_by=override.get("by"),
            set_at=override.get("at"),
        )
    return out


def _needs_me(client: Client, tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {
            "kind": "escalation",
            "id": t["id"],
            "subject": t["subject"],
            "escalate_reason": t.get("escalate_reason"),
            "severity": t["severity"],
            "updated_at": t.get("updated_at"),
        }
        for t in tickets
        if t["assignee"] == ASSIGNEE_JEANNE and t["status"] != STATUS_RESOLVED
    ]
    summary = checklist_summary(client.settings)
    if summary["ready"] and not summary["signed_off_at"]:
        items.append(
            {
                "kind": "launch_sign_off",
                "id": f"launch_{client.id}",
                "subject": "Launch checklist is complete — waiting on your sign-off",
                "updated_at": None,
            }
        )
    rules = get_rules(client.settings)
    pricing = None
    if not rules.get("configured"):
        pricing = "No resale price set for this client yet"
    elif rules.get("placeholder"):
        pricing = "Resale price still on placeholder values — confirm with Venu"
    if pricing:
        items.append(
            {"kind": "pricing", "id": f"pricing_{client.id}", "subject": pricing, "updated_at": None}
        )
    return items


def _history(client: Client, tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    onboarding = client.settings.onboarding or {}
    launch = dict(onboarding.get("launch") or {})
    events: list[dict[str, Any]] = []

    def add(at: Any, kind: str, text: str) -> None:
        if at:
            events.append({"at": str(at), "kind": kind, "text": text})

    add(
        onboarding.get("handoff_at"),
        "sale",
        f"Sale handed off ({onboarding.get('handoff_source') or 'manual'}) with the assessment",
    )
    add(get_profile(client.settings).configured_at, "setup", "First-client setup applied")
    if launch.get("signed_off_at"):
        add(
            launch["signed_off_at"],
            "launch",
            "Launch signed off" + (" (conditional)" if launch.get("conditional") else ""),
        )
    for order in onboarding.get("change_orders") or []:
        if isinstance(order, dict):
            add(
                order.get("at"),
                "change_order",
                f"Change order {order.get('reference', '')}: added {order.get('domain', '')}",
            )
    override = _portfolio_blob(client).get("override") or {}
    if override.get("at") and override.get("note"):
        add(override["at"], "owner_note", f"Your note: {override['note']}")
    for t in tickets:
        for event in t.get("history") or []:
            if event.get("event") in {"created", "escalated", "returned"} or (
                event.get("event") == "status" and "RESOLVED" in str(event.get("note"))
            ):
                label = {
                    "created": "Ticket opened",
                    "escalated": "Escalated to you",
                    "returned": "Returned to Cepoch",
                }.get(event["event"], "Ticket resolved")
                add(event.get("at"), "support", f"{label}: {t['subject']}")
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:HISTORY_LIMIT]


def save_portfolio(
    *,
    client_id: str,
    implementation_status: Optional[str],
    account_risk: Optional[str],
    owner_notes: str,
    ctx_user_id: str,
) -> dict[str, Any]:
    """Record Jeanne's override. ``None`` or "" for a field returns it to derived."""
    status = (implementation_status or "").strip().casefold() or None
    if status and status not in STATUSES:
        raise ValueError(f"implementation_status must be one of {', '.join(STATUSES)}")
    risk = (account_risk or "").strip().casefold() or None
    if risk and risk not in RISKS:
        raise ValueError(f"account_risk must be one of {', '.join(RISKS)}")

    client = control.get_client(client_id)
    onboarding = dict(client.settings.onboarding or {})
    now = utcnow().isoformat()
    portfolio = {
        "override": {
            "implementation_status": (
                {"value": status, "by": ctx_user_id, "at": now} if status else None
            ),
            "account_risk": {"value": risk, "by": ctx_user_id, "at": now} if risk else None,
            "note": (owner_notes or "")[:2000],
            "by": ctx_user_id,
            "at": now,
        }
    }
    onboarding["portfolio"] = portfolio
    payload = client.settings.to_dict()
    payload["onboarding"] = onboarding
    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx_user_id
    )
    return portfolio


def _client_row(client: Client, now: datetime) -> dict[str, Any]:
    tickets = list_tickets(client_id=client.id)
    override = _portfolio_blob(client).get("override") or {}
    status = _with_override(derive_status(client, tickets), override.get("implementation_status"))
    risk = _with_override(derive_risk(client, tickets, status["value"], now), override.get("account_risk"))
    needs = _needs_me(client, tickets)
    rules = get_rules(client.settings)
    return {
        "client_id": client.id,
        "slug": client.slug,
        "company_name": client.company_name,
        "status": client.status,
        "purchased_domains": list(client.settings.purchased_domains or []),
        "enabled_domains": list(client.settings.enabled_domains or []),
        "implementation_status": status["value"],
        "implementation": status,
        "account_risk": risk["value"],
        "risk": risk,
        "owner_notes": override.get("note") or "",
        "needs_me": needs,
        "needs_me_count": len(needs),
        "history": _history(client, tickets),
        "revenue": {
            "placeholder": bool(rules.get("placeholder")),
            "customer_price_usd": rules.get("customer_price_usd"),
            "margin_percent": rules.get("margin_percent"),
            "quote_status": rules.get("quote_status"),
            "disclaimer": rules.get("disclaimer"),
        },
    }


def owner_portfolio() -> dict[str, Any]:
    """Aggregate exception-only owner view across clients (control metadata only)."""
    clients = [
        c
        for c in control.list_clients()
        if c.status == "ACTIVE" and not getattr(c.settings, "internal_workspace", False)
    ]
    now = utcnow()
    rows = [_client_row(c, now) for c in clients]
    # Exceptions first: at-risk, then watch, then most needs-me.
    order = {"at_risk": 0, "watch": 1, "healthy": 2}
    rows.sort(key=lambda r: (order.get(r["account_risk"], 3), -r["needs_me_count"], r["company_name"]))
    queue = [
        {**item, "client_id": r["client_id"], "company_name": r["company_name"]}
        for r in rows
        for item in r["needs_me"]
    ]
    priced = [r["revenue"]["customer_price_usd"] for r in rows if r["revenue"]["customer_price_usd"]]
    return {
        "disclaimer": (
            "FB-047 JeanneCAIO exception-only owner view — not a CRM. "
            "Routine support stays with Cepoch (FB-046)."
        ),
        "client_count": len(rows),
        "needs_me_count": len(queue),
        "at_risk_count": sum(1 for r in rows if r["account_risk"] == "at_risk"),
        "watch_count": sum(1 for r in rows if r["account_risk"] == "watch"),
        "live_count": sum(1 for r in rows if r["implementation_status"] == "live"),
        "revenue_total_usd": sum(priced) if priced else None,
        "revenue_placeholder": any(r["revenue"]["placeholder"] for r in rows),
        "clients": rows,
        "needs_me_queue": queue[:30],
    }
