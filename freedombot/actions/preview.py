"""Human-readable content for approval cards.

The action payload is tool arguments (often just ``customer`` / ``limit``). Approvers
need to see *what* they are deciding on — amounts, line items, email body, etc. —
so this module resolves that from the payload, live workspace data, or the
action's result after it ran.
"""

from __future__ import annotations

from typing import Any, Optional

from freedombot.actions.models import Action
from freedombot.tenancy.context import TenantContext


def content_for(action: Action, *, ctx: TenantContext,
                schedules: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
    """Structured preview for one action. Always safe to put on the API.

    ``schedules`` (id -> scheduled task, from :func:`schedule_ids`) saves one read per card on a list.
    """
    payload = action.payload or {}
    tool = action.tool or ""
    result = (action.result or {}).get("data") if isinstance(action.result, dict) else None
    if not isinstance(result, dict):
        result = {}
    content = _content(action, tool, payload, result, ctx)
    content["line"] = _line(tool, payload, content)
    content["schedule"] = _schedule_status(action, payload, result, ctx, schedules)
    return content


# --------------------------------------------------------------------------- scheduled tasks

_SCHEDULE_TOOLS = {"schedule_create", "schedule_update", "schedule_cancel"}
#: What the approver sees about the schedule; the instruction is the request itself.
_SCHEDULE_KEYS = ("id", "title", "instruction", "schedule", "status", "next_run_at", "last_run_at", "last_status",
                  "last_result", "run_count", "created_by", "session_id")


def schedule_id(action: Action) -> Optional[str]:
    """The scheduled task this request is about, or was made by, if any."""
    tool = action.tool or ""
    result = (action.result or {}).get("data") if isinstance(action.result, dict) else None
    result = result if isinstance(result, dict) else {}
    if tool == "schedule_create":
        found = result.get("id")
    elif tool in {"schedule_cancel", "schedule_update"}:
        found = (action.payload or {}).get("schedule_id") or result.get("id")
    else:
        found = action.workflow_id
    found = str(found or "").strip()
    return found if found.startswith("sch_") else None


def schedule_ids(actions: list[Action]) -> set[str]:
    return {i for i in (schedule_id(a) for a in actions) if i}


def _schedule_status(action: Action, payload: dict[str, Any], result: dict[str, Any], ctx: TenantContext,
                     schedules: Optional[dict[str, dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """The schedule behind this card and how it stands now (active, stopped, next and last run)."""
    from freedombot import automation

    tool = action.tool or ""
    task_id = schedule_id(action)
    if task_id is None and tool != "schedule_create":
        return None
    if task_id is not None:
        if schedules is None:
            schedules = automation.lookup(ctx, [task_id])
        task = schedules.get(task_id)
        if task is not None:
            info = {k: task.get(k) for k in _SCHEDULE_KEYS}
            info["last_result"] = str(info.get("last_result") or "")[:300]
            info["made_by_run"] = tool not in _SCHEDULE_TOOLS
            return info
    # Not saved yet (still waiting, rejected or withdrawn): what it would be, from the request itself.
    info = {k: None for k in _SCHEDULE_KEYS}
    info.update(id=task_id, title=payload.get("title") or payload.get("instruction"),
                instruction=payload.get("instruction"), schedule=_planned(payload), status="not_set_up",
                run_count=0, created_by=action.requested_by, made_by_run=tool not in _SCHEDULE_TOOLS)
    return info


def _planned(payload: dict[str, Any]) -> Optional[str]:
    """'every Mon at 09:00 (Asia/Kolkata)' for a schedule request that is not saved yet."""
    from freedombot import automation

    try:
        frequency = str(payload.get("frequency") or "").strip().lower()
        days = automation.parse_weekdays(payload.get("weekdays") or ()) if frequency == automation.WEEKLY else ()
        every = automation.parse_every(payload.get("every_minutes")) if frequency == automation.INTERVAL else None
        starts = str(payload.get("time") or "").strip()
        return automation.describe({
            "frequency": frequency, "weekdays": days, "every_minutes": every,
            "time_of_day": automation.parse_time(starts) if starts or frequency != automation.INTERVAL else "",
            "timezone": str(payload.get("timezone") or "workspace time")})
    except (automation.ScheduleError, ValueError, TypeError):
        return None


def _schedule_request(tool: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """A request to set up or stop a recurring task: what it does and how often."""
    what = str(payload.get("instruction") or "").strip()
    title = str(payload.get("title") or result.get("title") or what[:60] or "Recurring task").strip()
    fields = []
    if what:
        fields.append({"label": "Each time it will", "value": _short(what)})
    when = _planned(payload) or result.get("schedule")
    if when:
        fields.append({"label": "How often", "value": str(when)})
    if tool == "schedule_cancel":
        headline = f"Stop the recurring task {payload.get('schedule_id') or ''}".strip()
        lines = ["It will not run again. Requests it already made stay in this list."]
    elif tool == "schedule_update":
        headline = f"Change the recurring task {payload.get('schedule_id') or ''}".strip()
        fields = _schedule_edits(payload)
        lines = ["Only these change; the schedule keeps its id and past runs. Each run is still checked like "
                 "a chat message."]
    else:
        headline = f"Recurring task: {title}"
        lines = ["Each run is checked as the person who asked, like a chat message: anything that needs "
                 "approval comes back here every time."]
    return {"kind": "schedule", "headline": headline[:200], "fields": fields, "lines": lines,
            "summary": headline[:200]}


def _schedule_edits(payload: dict[str, Any]) -> list[dict[str, str]]:
    """What a change request sets, in plain words."""
    from freedombot import automation

    out = []
    if payload.get("title"):
        out.append({"label": "New name", "value": _short(payload["title"])})
    if payload.get("instruction"):
        out.append({"label": "Each time it will", "value": _short(payload["instruction"])})
    frequency = str(payload.get("frequency") or "").strip().lower()
    if payload.get("every_minutes") not in (None, ""):
        try:
            out.append({"label": "How often", "value": automation.describe(
                {"frequency": automation.INTERVAL, "every_minutes": automation.parse_every(payload["every_minutes"])})})
        except automation.ScheduleError:
            out.append({"label": "How often", "value": f"every {payload['every_minutes']} minutes"})
    elif frequency:
        out.append({"label": "How often", "value": {"daily": "every day", "weekly": "chosen weekdays"}.get(
            frequency, frequency)})
    if payload.get("weekdays"):
        days = payload["weekdays"]
        out.append({"label": "Weekdays", "value": ", ".join(days) if isinstance(days, list) else str(days)})
    if payload.get("time"):
        out.append({"label": "Time", "value": str(payload["time"])})
    if payload.get("timezone"):
        out.append({"label": "Timezone", "value": str(payload["timezone"])})
    return out


def _content(
    action: Action, tool: str, payload: dict[str, Any], result: dict[str, Any], ctx: TenantContext
) -> dict[str, Any]:
    if tool in _SCHEDULE_TOOLS:
        return _schedule_request(tool, payload, result)
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
    """An app action (Google apps, YouTube, HubSpot, Facebook, Instagram, LinkedIn, Zoom, Apollo): which action, and every argument it will send."""
    from freedombot.connectors.composio import app_name
    from freedombot.connectors.composio_actions import toolkit_of

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
            from freedombot.tools.base import RunScope
            from freedombot.control import store as control

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
    "memory_save_attachment": "Save file to memory",
    "crm_create_note": "CRM note",
    "crm_create_task": "CRM task",
    "crm_tag_lead": "Tag contact",
    "calendar_cancel_event": "Cancel event",
}

#: The payload value that says what the request is about (first one present wins).
_DETAIL_KEYS = (
    "title", "subject", "name", "decision", "new_content", "content", "body", "question", "topic",
    "customer", "company", "query", "add_tags", "event_id", "record_id", "contact_id", "deal_id", "schedule_id",
)

#: The same, for app actions (their arguments use each app's own names).
_APP_DETAIL_KEYS = ("recipient_email", "to", "recipients", "subject", "title", "summary", "name", "text", "query")


def _line(tool: str, payload: dict[str, Any], content: dict[str, Any]) -> str:
    """One short line for the chat's approval list: what will happen, and to whom or to what."""
    from freedombot.rbac.registry import get_spec

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
