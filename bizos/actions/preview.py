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
    content = _content(action, tool, payload, result, ctx)
    content["line"] = _line(tool, payload, content)
    return content


def _content(
    action: Action, tool: str, payload: dict[str, Any], result: dict[str, Any], ctx: TenantContext
) -> dict[str, Any]:
    if tool == "finance_draft_invoice":
        return _invoice(action, payload, result, ctx)
    if tool.startswith("email_") or tool in {"gmail_send", "gmail_create_draft"}:
        return _email(payload, result)
    if tool.startswith("crm_"):
        return _crm(tool, payload, result)
    if tool == "n8n_trigger_webhook":
        return _n8n(payload)
    if tool in {"integration_write_action", "integration_destructive_action"}:
        return _app_action(tool, payload)
    return _generic(payload, result)


def _app_action(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    """An app action (Gmail, Calendar, YouTube, HubSpot): which action, and every argument it will send."""
    from bizos.connectors.composio import app_name
    from bizos.connectors.composio_actions import toolkit_of

    action = str(payload.get("action") or "").upper()
    toolkit = toolkit_of(action) or action.split("_", 1)[0].lower()
    app = app_name(toolkit) if toolkit else "App"
    what = action.split("_", 1)[1].replace("_", " ").capitalize() if "_" in action else action
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    fields = [{"label": "App", "value": app}, {"label": "Action", "value": action}]
    fields += [{"label": str(k).replace("_", " ").title(), "value": _short(v)}
               for k, v in arguments.items() if v not in (None, "", [], {})]
    headline = f"{app}: {what}"
    if tool == "integration_destructive_action":
        headline += " (deletes, sends or publishes; cannot be undone)"
    return {"kind": "app_action", "headline": headline[:200], "fields": fields[:14], "lines": [],
            "summary": headline[:200]}


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
            from app.assistant.company_tools import finance_list_pending_billables
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


#: Longest one-line summary on the chat's approval list; the card's fields carry the rest.
_LINE_CHARS = 80

#: What the request does, for tools whose registry title reads badly on one line.
_VERBS = {
    "memory_add_fact": "Save to memory",
    "memory_add_sop": "Save SOP",
    "memory_record_decision": "Record decision",
    "memory_correct": "Correct memory",
    "crm_create_note": "CRM note",
    "crm_create_task": "CRM task",
    "crm_tag_lead": "Tag contact",
    "calendar_cancel_event": "Cancel event",
}

#: The payload value that says what the request is about (first one present wins).
_DETAIL_KEYS = (
    "title", "subject", "name", "decision", "new_content", "content", "body", "question", "topic",
    "customer", "company", "query", "add_tags", "event_id", "record_id", "contact_id", "deal_id",
)

#: The same, for app actions (their arguments use each app's own names).
_APP_DETAIL_KEYS = ("recipient_email", "to", "recipients", "subject", "title", "summary", "name", "text", "query")


def _line(tool: str, payload: dict[str, Any], content: dict[str, Any]) -> str:
    """One short line for the chat's approval list: what will happen, and to whom or to what."""
    from bizos.rbac.registry import get_spec

    fields = {f["label"]: f["value"] for f in content.get("fields") or []}
    kind = content.get("kind")
    if kind == "invoice":
        customer = fields.get("Customer")
        text = f"Invoice for {customer}, {fields['Amount']}" if customer not in (None, "—") else f"Invoice, {fields['Amount']}"
    elif kind == "email":
        text = f"Email to {_people(fields.get('To'))}: {fields.get('Subject')}"
    elif kind == "app_action":
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        detail = _first(arguments, _APP_DETAIL_KEYS)
        text = content["headline"].split(" (")[0] + (f" · {detail}" if detail else "")
    elif tool == "crm_create_contact":
        name = " ".join(x for x in (_first(payload, ("first_name",)), _first(payload, ("last_name",))) if x)
        company = _first(payload, ("company",))
        text = f"New contact: {name or _first(payload, ('email',)) or 'unnamed'}" + (f" ({company})" if company else "")
    elif tool in {"crm_update_contact_field", "crm_bulk_update_contacts"}:
        who = f" on {payload['lifecycle_stage']} contacts" if payload.get("lifecycle_stage") else ""
        text = f"Set contact {payload.get('field') or 'field'} to {payload.get('value')}{who}"
    elif tool == "crm_update_deal_stage":
        text = f"Move deal to {payload.get('stage')}"
    elif tool == "crm_delete_record":
        text = f"Delete {payload.get('record_type') or 'record'} {payload.get('record_id') or ''}"
    elif tool == "memory_escalate_conflict":
        text = f"Memory conflict on {payload.get('topic') or 'policy'}: {payload.get('question') or ''}"
    elif tool in {"calendar_create_event", "calendar_reschedule_event"} and payload.get("starts_at"):
        when = _when(payload["starts_at"])
        if tool == "calendar_create_event":
            text = f"New event: {payload.get('title') or 'untitled'}, {when}"
        else:
            text = f"Move event to {when}"
    else:
        spec = get_spec(tool)
        verb = _VERBS.get(tool) or (spec.title if spec else tool.replace("_", " ").capitalize())
        detail = _first(payload, _DETAIL_KEYS)
        text = f"{verb}: {detail}" if detail else verb
    text = " ".join(text.split()).rstrip(": ")
    return text if len(text) <= _LINE_CHARS else text[: _LINE_CHARS - 1].rstrip() + "…"


def _first(values: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = values.get(key)
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value if v not in (None, ""))
        if value not in (None, "", {}) and not isinstance(value, dict):
            return str(value)
    return ""


def _people(to: Optional[str]) -> str:
    """"a@x.com, b@y.com, c@z.com" -> "a@x.com +2"."""
    names = [n.strip() for n in str(to or "").split(",") if n.strip() and n.strip() != "—"]
    if not names:
        return "recipient"
    return names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")


def _when(value: Any) -> str:
    """An ISO time as "3 Dec 10:00", in the time zone it was written in."""
    from datetime import datetime

    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:16]
    return f"{moment.day} {moment:%b %H:%M}"


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = str(value)
        return text[:180] + ("…" if len(text) > 180 else "")
    return str(value)[:400]
