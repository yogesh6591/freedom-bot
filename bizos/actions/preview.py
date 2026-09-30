"""Human-readable content for approval cards.

The action payload is tool arguments (often just ``customer`` / ``limit``). Approvers
need to see *what* they are deciding on — amounts, line items, email body, etc. —
so this module resolves that from the payload, live workspace data, or the
action's result after it ran.
"""

from __future__ import annotations

from typing import Any, Optional

from bizos.actions.models import Action
from bizos.tenancy.context import TenantContext


def content_for(action: Action, *, ctx: TenantContext) -> dict[str, Any]:
    """Structured preview for one action. Always safe to put on the API."""
    payload = action.payload or {}
    tool = action.tool or ""
    result = (action.result or {}).get("data") if isinstance(action.result, dict) else None
    if not isinstance(result, dict):
        result = {}

    if tool == "finance_draft_invoice":
        return _invoice(action, payload, result, ctx)
    if tool.startswith("email_") or tool in {"gmail_send", "gmail_create_draft"}:
        return _email(payload, result)
    if tool.startswith("crm_"):
        return _crm(tool, payload, result)
    if tool == "n8n_trigger_webhook":
        return _n8n(payload)
    return _generic(payload, result)


def _invoice(
    action: Action, payload: dict[str, Any], result: dict[str, Any], ctx: TenantContext
) -> dict[str, Any]:
    snap = payload.get("_content_snapshot") if isinstance(payload.get("_content_snapshot"), dict) else {}
    customer = str(
        result.get("customer")
        or snap.get("customer")
        or payload.get("customer")
        or payload.get("company")
        or ""
    ).strip()
    items = list(result.get("line_items") or snap.get("line_items") or [])
    total = result.get("total") if result.get("total") is not None else snap.get("total")
    number = result.get("number")

    # Pending requests without a snapshot: resolve billables live.
    if not items:
        try:
            from bizos.tools.domains import finance_list_pending_billables
            from bizos.tools.base import RunScope
            from bizos.control import store as control

            settings = control.get_client(ctx.client_id).settings
            scope = RunScope(ctx=ctx, settings=settings, domain="finance")
            listed = finance_list_pending_billables(
                scope, {"customer": customer, "limit": int(payload.get("limit") or 50)}
            )
            data = listed.data or {}
            items = list(data.get("items") or [])
            if total is None:
                total = data.get("total")
        except Exception:
            items = []

    if total is None:
        total = sum(float(i.get("amount") or 0) for i in items)
    try:
        total_f = float(total or 0)
    except (TypeError, ValueError):
        total_f = 0.0

    lines = [
        f"{i.get('name') or i.get('deal_id') or 'line'}: "
        f"${float(i.get('amount') or 0):,.2f} {i.get('currency') or 'USD'}"
        for i in items
    ]
    fields = [
        {"label": "Customer", "value": customer or "—"},
        {"label": "Amount", "value": f"${total_f:,.2f} USD"},
        {"label": "Line items", "value": str(len(items))},
    ]
    if number:
        fields.insert(1, {"label": "Invoice", "value": str(number)})

    headline = (
        f"Draft invoice for {customer or 'customer'} — ${total_f:,.2f}"
        if customer
        else f"Draft invoice — ${total_f:,.2f}"
    )
    if number:
        headline = f"{number}: {headline}"

    return {
        "kind": "invoice",
        "headline": headline,
        "fields": fields,
        "lines": lines,
        "summary": (
            f"{len(items)} pending billable(s) totaling ${total_f:,.2f}"
            if items
            else (str((action.result or {}).get("summary") or action.description or headline))
        ),
    }


def _email(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    to = payload.get("to") or payload.get("recipients") or result.get("to")
    if isinstance(to, list):
        to = ", ".join(str(x) for x in to)
    subject = str(payload.get("subject") or result.get("subject") or "(no subject)")
    body = str(payload.get("body") or payload.get("text") or "")
    fields = [
        {"label": "To", "value": str(to or "—")},
        {"label": "Subject", "value": subject},
    ]
    lines = [body[:500] + ("…" if len(body) > 500 else "")] if body else []
    return {
        "kind": "email",
        "headline": f"Email to {to or 'recipient'}: {subject}",
        "fields": fields,
        "lines": lines,
        "summary": f"Email “{subject}” to {to or '—'}",
    }


def _crm(tool: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title") or payload.get("name") or tool)
    fields = []
    for key, label in (
        ("title", "Title"),
        ("name", "Name"),
        ("contact_id", "Contact"),
        ("deal_id", "Deal"),
        ("stage", "Stage"),
        ("due_at", "Due"),
        ("body", "Note"),
        ("description", "Description"),
    ):
        if payload.get(key) not in (None, ""):
            fields.append({"label": label, "value": str(payload[key])[:400]})
    if result:
        for key in ("id", "summary"):
            if result.get(key):
                fields.append({"label": key.replace("_", " ").title(), "value": str(result[key])})
    return {
        "kind": "crm",
        "headline": title,
        "fields": fields or [{"label": "Tool", "value": tool}],
        "lines": [],
        "summary": title,
    }


def _n8n(payload: dict[str, Any]) -> dict[str, Any]:
    event = str(payload.get("event") or "webhook")
    workflow = str(payload.get("workflow") or "")
    fields = [{"label": "Event", "value": event}]
    if workflow:
        fields.append({"label": "Workflow", "value": workflow})
    return {
        "kind": "automation",
        "headline": f"Trigger automation: {event}",
        "fields": fields,
        "lines": [],
        "summary": f"n8n event {event}",
    }


def _generic(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    skip = {"limit", "offset", "dry_run"}
    fields = [
        {"label": str(k).replace("_", " ").title(), "value": _short(v)}
        for k, v in payload.items()
        if k not in skip and v not in (None, "", [], {})
    ]
    if result.get("summary"):
        fields.append({"label": "Result", "value": str(result["summary"])})
    headline = ", ".join(f"{f['label']}: {f['value']}" for f in fields[:3]) or "Proposed action"
    return {
        "kind": "generic",
        "headline": headline[:200],
        "fields": fields[:12],
        "lines": [],
        "summary": headline[:200],
    }


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = str(value)
        return text[:180] + ("…" if len(text) > 180 else "")
    return str(value)[:400]
