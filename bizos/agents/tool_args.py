"""
Tool argument schemas and normalization
=======================================

Agno's ``@tool`` decorator infers JSON Schema from the Python signature. Our
generic wrappers used ``**kwargs``, which Agno publishes as::

    {"properties": {"kwargs": {"type": "object", "properties": {},
                               "additionalProperties": false}}}

Models then call tools with an empty nested ``kwargs`` object. Writes "succeed"
with blank payloads (memory key collapses to ``client``, emails queue with no
recipients, etc.).

This module:
1. Publishes real JSON Schemas so the model sees named parameters.
2. Unwraps a leftover nested ``kwargs`` bag if one still arrives.
"""

from __future__ import annotations

from typing import Any


def normalize_tool_arguments(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten Agno/model quirks into a plain argument dict."""
    args = dict(raw or {})
    # Single nested bag from the broken **kwargs schema (or a stubborn model).
    for _ in range(2):
        if set(args.keys()) == {"kwargs"} and isinstance(args.get("kwargs"), dict):
            args = dict(args["kwargs"])
        else:
            break
    return args


def _s(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _i(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _f(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def _arr(description: str, item_type: str = "string") -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": item_type},
    }


def _obj(
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    additional: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional,
    }
    if required:
        schema["required"] = required
    return schema


#: Fallback when a tool has no dedicated schema: allow any named fields.
OPEN_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


TOOL_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "memory_search": _obj(
        {
            "query": _s("Search text"),
            "categories": _arr("Optional FACT|SOP|DECISION|CORRECTION filters"),
            "limit": _i("Max hits"),
            "domain": _s("Optional domain filter"),
            "authoritative_only": {
                "type": "boolean",
                "description": "If true (default), only human-approved values",
            },
        },
        ["query"],
    ),
    "memory_get_fact": _obj(
        {
            "key": _s("Stable fact key, e.g. refund_period"),
            "category": _s("FACT (default), SOP, DECISION, or CORRECTION"),
        },
        ["key"],
    ),
    "memory_history": _obj({"item_id": _s("Memory item id")}, ["item_id"]),
    "memory_add_fact": _obj(
        {
            "title": _s("Short human label for the fact"),
            "content": _s("The fact text to store (required, non-empty)"),
            "key": _s("Stable lookup key, e.g. refund_period or e2e_probe_0910"),
            "tags": _arr("Optional tags"),
            "source_type": _s("MANUAL (default), DOCUMENT, AI_INFERENCE, …"),
            "confidence": _f("0-1 confidence"),
        },
        ["title", "content"],
    ),
    "memory_add_sop": _obj(
        {
            "title": _s("SOP title"),
            "content": _s("Procedure text"),
            "key": _s("Stable lookup key"),
            "tags": _arr("Optional tags"),
        },
        ["title", "content"],
    ),
    "memory_record_decision": _obj(
        {
            "title": _s("Decision title"),
            "decision": _s("What was decided"),
            "content": _s("Optional alias for decision text"),
            "decided_on": _s("ISO date"),
            "approved_by": _s("Who approved"),
            "context": _s("Why it came up"),
            "reason": _s("Rationale"),
            "status": _s("active|superseded|…"),
            "key": _s("Stable lookup key"),
        },
        ["title", "decision"],
    ),
    "memory_correct": _obj(
        {
            "key": _s("Fact key to correct"),
            "item_id": _s("Or memory item id"),
            "new_content": _s("Replacement value"),
            "content": _s("Alias for new_content"),
            "reason": _s("Why this correction"),
        },
        ["new_content", "reason"],
    ),
    "knowledge_search": _obj(
        {"query": _s("Search query"), "limit": _i("Max hits")},
        ["query"],
    ),
    "crm_search_contacts": _obj(
        {
            "query": _s("Name or email fragment"),
            "lifecycle": _s("Optional lifecycle filter"),
            "limit": _i("Max hits"),
        }
    ),
    "crm_get_contact": _obj(
        {"contact_id": _s("Contact id or email")},
        ["contact_id"],
    ),
    "crm_search_companies": _obj(
        {"query": _s("Company name or domain"), "limit": _i("Max hits")}
    ),
    "crm_search_deals": _obj(
        {
            "stage": _s("Optional stage"),
            "owner": _s("Optional owner"),
            "limit": _i("Max hits"),
        }
    ),
    "crm_pipeline_summary": _obj({}),
    "crm_create_note": _obj(
        {
            "body": _s("Note text"),
            "contact_id": _s("Contact id"),
            "deal_id": _s("Deal id"),
        },
        ["body"],
    ),
    "crm_create_task": _obj(
        {
            "title": _s("Task title"),
            "body": _s("Details"),
            "contact_id": _s("Contact id"),
            "assignee": _s("Assignee"),
            "due_at": _s("ISO datetime"),
        },
        ["title"],
    ),
    "crm_tag_lead": _obj(
        {
            "contact_id": _s("Contact id"),
            "add_tags": _arr("Tags to add"),
            "remove_tags": _arr("Tags to remove"),
            "lifecycle": _s("Optional new lifecycle"),
        },
        ["contact_id"],
    ),
    "crm_update_contact_field": _obj(
        {
            "contact_id": _s("Contact id"),
            "field": _s("Field name"),
            "value": _s("New value"),
        },
        ["contact_id", "field", "value"],
    ),
    "crm_create_contact": _obj(
        {
            "first_name": _s("First name"),
            "last_name": _s("Last name"),
            "email": _s("Email"),
            "company": _s("Company"),
            "title": _s("Job title"),
            "lifecycle": _s("Lifecycle stage"),
            "tags": _arr("Tags"),
        },
        ["first_name"],
    ),
    "crm_update_deal_stage": _obj(
        {"deal_id": _s("Deal id"), "stage": _s("New stage")},
        ["deal_id", "stage"],
    ),
    "crm_bulk_update_contacts": _obj(
        {
            "field": _s("Field name"),
            "value": _s("New value"),
            "lifecycle_filter": _s("Only contacts in this lifecycle"),
        },
        ["field", "value"],
    ),
    "crm_delete_record": _obj(
        {
            "record_type": _s("contact|deal|company"),
            "record_id": _s("Record id"),
        },
        ["record_type", "record_id"],
    ),
    "email_search_threads": _obj(
        {"query": _s("Search query"), "limit": _i("Max hits")}
    ),
    "email_read_thread": _obj({"thread_id": _s("Thread id")}, ["thread_id"]),
    "email_create_draft": _obj(
        {
            "recipients": _arr("To addresses"),
            "subject": _s("Subject"),
            "body": _s("Body"),
            "cc": _arr("Cc addresses"),
        },
        ["recipients", "subject", "body"],
    ),
    "email_send_message": _obj(
        {
            "recipients": _arr("To addresses"),
            "subject": _s("Subject"),
            "body": _s("Body"),
            "cc": _arr("Cc addresses"),
        },
        ["recipients", "subject", "body"],
    ),
    "calendar_list_events": _obj(
        {
            "start": _s("ISO start"),
            "end": _s("ISO end"),
            "limit": _i("Max events"),
        }
    ),
    "calendar_check_availability": _obj(
        {
            "start": _s("ISO start"),
            "end": _s("ISO end"),
            "slot_minutes": _i("Slot size"),
        },
        ["start", "end"],
    ),
    "calendar_create_event": _obj(
        {
            "title": _s("Event title"),
            "starts_at": _s("ISO start"),
            "ends_at": _s("ISO end"),
            "attendees": _arr("Attendee emails"),
            "description": _s("Description"),
            "location": _s("Location"),
        },
        ["title", "starts_at", "ends_at"],
    ),
    "calendar_reschedule_event": _obj(
        {
            "event_id": _s("Event id"),
            "starts_at": _s("ISO start"),
            "ends_at": _s("ISO end"),
        },
        ["event_id", "starts_at", "ends_at"],
    ),
    "calendar_cancel_event": _obj({"event_id": _s("Event id")}, ["event_id"]),
    "request_human_review": _obj(
        {
            "question": _s("What the human must decide"),
            "context": _s("Background"),
            "choices": _arr("Options"),
            "recommended_option": _s("Suggested choice"),
            "confidence": _f("0-1"),
        },
        ["question", "context"],
    ),
}


def schema_for_tool(name: str) -> dict[str, Any]:
    """JSON Schema exposed to the model for one tool."""
    return TOOL_PARAMETER_SCHEMAS.get(name, OPEN_OBJECT_SCHEMA)
