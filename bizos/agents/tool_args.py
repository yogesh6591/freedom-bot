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


def _enum(description: str, values: list[str]) -> dict[str, Any]:
    return {"type": "string", "description": description, "enum": values}


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
            "topic": _s("Policy topic, e.g. discount; differing values on one topic are a conflict"),
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
    "memory_escalate_conflict": _obj(
        {
            "topic": _s("The policy topic that conflicts, e.g. discount"),
            "item_ids": _arr("Ids of the conflicting memory items"),
            "question": _s("The decision the person needs to make"),
        },
        ["topic", "question"],
    ),
    "support_create_ticket": _obj(
        {
            "subject": _s("One-line summary of the problem"),
            "body": _s("What happened, what the user expected, any error text"),
            "severity": _s("NORMAL (default), HIGH (blocking work) or CRITICAL (platform down)"),
        },
        ["subject"],
    ),
    "support_my_tickets": _obj({}),
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
            "deal_id": _s("Deal id from crm_search_deals, to link the task to a deal"),
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
            "field": _enum(
                "Contact field to set",
                ["owner", "lifecycle", "title", "company", "phone", "email", "first_name", "last_name"],
            ),
            "value": _s("New value"),
            "lifecycle_stage": _s(
                "Which contacts: one lifecycle stage such as lead or customer. Pass ALL only when the "
                "user clearly means every contact in the CRM."
            ),
        },
        ["field", "value", "lifecycle_stage"],
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
    "youtube_search_videos": _obj(
        {
            "query": _s("Search terms"),
            "limit": _i("Max results (default 5)"),
            "order": _s("relevance|date|viewCount|rating"),
        },
        ["query"],
    ),
    "youtube_my_channel": _obj({}),
    "youtube_my_videos": _obj({"limit": _i("Max videos (default 10)")}),
    "youtube_video_details": _obj({"video_ids": _arr("YouTube video ids")}, ["video_ids"]),
    "youtube_video_comments": _obj(
        {
            "video_id": _s("YouTube video id"),
            "limit": _i("Max comments (default 20)"),
            "order": _s("relevance|time"),
        },
        ["video_id"],
    ),
    "strategy_list_priorities": _obj({"query": _s("Optional search")}),
    "strategy_record_option": _obj(
        {
            "title": _s("Option title"),
            "content": _s("Option body"),
            "option": _s("Alias for content"),
            "pros": _s("Pros"),
            "cons": _s("Cons"),
            "recommended": {"type": "boolean"},
            "key": _s("Optional memory key"),
        },
        ["title"],
    ),
    "finance_lookup_policy": _obj({"query": _s("Policy search")}),
    "finance_list_pending_billables": _obj(
        {
            "customer": _s("Customer / company name"),
            "company": _s("Alias for customer"),
            "limit": _i("Max rows"),
        }
    ),
    "finance_draft_invoice": _obj(
        {
            "customer": _s("Customer / company name"),
            "company": _s("Alias for customer"),
            "limit": _i("Max line items"),
        },
        ["customer"],
    ),
    "finance_propose_adjustment": _obj(
        {
            "customer": _s("Customer name"),
            "amount": _f("Amount"),
            "reason": _s("Why"),
            "kind": _s("adjustment|payment|credit"),
        },
        ["customer", "amount", "reason"],
    ),
    "hr_match_candidate": _obj(
        {
            "candidate_name": _s("Candidate name, e.g. Jordan Lee"),
            "opening": _s("Job opening / role name, e.g. Operations Coordinator"),
            "opening_key": _s("Job opening memory key, if known"),
            "resume_key": _s("Resume memory key, if known"),
            "resume_text": _s("Raw resume text if not already in memory"),
        }
    ),
    "approvals_list": _obj(
        {
            "status": _s(
                "pending (default) | drafts | approved | completed | failed | rejected | "
                "cancelled | all — several may be comma-separated"
            ),
            "mine": {"type": "boolean", "description": "Only actions this user requested"},
            "limit": _i("Max rows (default 25)"),
        }
    ),
    "approvals_get": _obj(
        {
            "action_id": _s("Action id, e.g. act_…"),
            "match": _s("Words from the action's title, when the id is not known"),
        }
    ),
    "approvals_approve": _obj(
        {
            "action_id": _s("Action id, e.g. act_…"),
            "match": _s("Words from the action's title, when the id is not known"),
            "comment": _s("Optional approval note"),
        }
    ),
    "approvals_reject": _obj(
        {
            "action_id": _s("Action id, e.g. act_…"),
            "match": _s("Words from the action's title, when the id is not known"),
            "comment": _s("Why it was rejected"),
        }
    ),
    "approvals_request_changes": _obj(
        {
            "action_id": _s("Action id, e.g. act_…"),
            "match": _s("Words from the action's title, when the id is not known"),
            "comment": _s("What needs to change"),
        },
        ["comment"],
    ),
    "approvals_submit": _obj(
        {
            "action_id": _s("Draft action id, e.g. act_…"),
            "match": _s("Words from the draft's title, when the id is not known"),
        }
    ),
    "admin_workspace_overview": _obj({}),
    "admin_list_users": _obj({}),
    "admin_get_setup": _obj({}),
    "admin_list_alerts": _obj({}),
    "admin_launch_checklist": _obj({}),
    "admin_pricing": _obj({}),
    "admin_list_domains": _obj({}),
    "admin_tool_permissions": _obj({"tool": _s("Optional tool name, title words or category, e.g. email")}),
    "admin_apply_setup": _obj(
        {
            "goals": _arr("Business goals (replaces the list)"),
            "systems": _arr("Systems the client uses (replaces the list)"),
            "sops": {
                "type": "array",
                "description": "SOPs to record: {title, content}",
                "items": {"type": "object", "properties": {"title": _s("Title"), "content": _s("Steps")}},
            },
            "roles_notes": _s("Who does what"),
            "access_notes": _s("Access notes"),
            "review_points": _arr("Where a human must review (replaces the list)"),
            "exclusions": _arr("Out-of-scope items (replaces the list)"),
            "autonomy_mode": _s("ADVISE | DRAFT | WAIT_FOR_APPROVAL | AUTO_WITHIN_SCOPE"),
            "enabled_domains": _arr("Purchased packs to enable"),
        }
    ),
    "admin_handoff_assessment": _obj(
        {"assessment": {"type": "object", "description": "Sale/assessment details as key/value pairs"}},
        ["assessment"],
    ),
    "admin_scan_alerts": _obj({}),
    "admin_dismiss_alert": _obj({"alert_id": _s("Alert id from admin_list_alerts")}, ["alert_id"]),
    "admin_set_baselines": _obj(
        {
            "items": {
                "type": "array",
                "description": "Baselines to add or update (matched by id, or workflow+metric)",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": _s("Existing id, to update"),
                        "workflow": _s("Workflow, e.g. Invoice approvals"),
                        "metric": _s("Metric, e.g. hours per week"),
                        "before_value": _s("Before"),
                        "after_value": _s("After"),
                        "unit": _s("Unit"),
                        "notes": _s("Notes"),
                    },
                },
            },
            "remove": _arr("Ids or workflow names to remove"),
        }
    ),
    "admin_mark_launch_item": _obj(
        {
            "item_id": _s("Checklist item id from admin_launch_checklist"),
            "status": _s("done | pending | waived"),
            "waive_reason": _s("Required when waiving"),
        },
        ["item_id"],
    ),
    "admin_launch_sign_off": _obj({"force": {"type": "boolean", "description": "Only if the user explicitly insists on signing off with items pending"}}),
    "admin_set_resale_rules": _obj(
        {
            "customer_price_usd": _f("Customer price in USD"),
            "cepoch_cost_usd": _f("Cepoch cost in USD"),
            "margin_percent": _f("Target margin %, derives cost when cost is not given"),
            "quote_status": _s("draft | pending | confirmed"),
            "notes": _s("Notes"),
            "scope_rules": {"type": "object", "description": "Consult scope weights to change, e.g. addon_usd, integration_usd, max_discount_percent"},
        }
    ),
    "admin_consult_quote": _obj(
        {
            "domains": _arr("Packs in scope"),
            "autonomy": _s("ADVISE | DRAFT | WAIT_FOR_APPROVAL | AUTO_WITHIN_SCOPE"),
            "integrations": _i("Number of integrations"),
            "discount_percent": _f("Discount %"),
            "label": _s("Label when saving"),
            "save": {"type": "boolean", "description": "Save the quote"},
        }
    ),
    "admin_create_user": _obj(
        {
            "email": _s("Email address"),
            "display_name": _s("Name"),
            "roles": _arr("VIEWER | DRAFTER | APPROVER | OPERATOR | ADMIN"),
            "data_access": _arr("Restricted data areas, e.g. finance, hr"),
        },
        ["email"],
    ),
    "admin_set_user_roles": _obj(
        {"user": _s("Email or name"), "roles": _arr("The full new set of roles")}, ["user", "roles"]
    ),
    "admin_set_user_access": _obj(
        {
            "user": _s("Email or name"),
            "data_access": _arr("The full new set of areas (omit to edit with add/remove)"),
            "add": _arr("Areas to grant"),
            "remove": _arr("Areas to revoke"),
        },
        ["user"],
    ),
    "admin_set_user_status": _obj(
        {"user": _s("Email or name"), "status": _s("ACTIVE | SUSPENDED")}, ["user", "status"]
    ),
    "admin_reset_password": _obj({"user": _s("Email or name")}, ["user"]),
    "admin_update_settings": _obj(
        {
            "default_execution_mode": _s("ADVISE | DRAFT | WAIT_FOR_APPROVAL | AUTO_WITHIN_SCOPE"),
            "order_dpa_allows": _arr("Full list of allowed regulated categories: phi, card, child, biometrics"),
            "llm_baa_covered": {"type": "boolean", "description": "Model vendor has a signed BAA"},
            "approval_policy": {"type": "object", "description": "Fields to change, e.g. allow_self_approval, approver_role, expires_after_hours"},
            "risk_policy": {"type": "object", "description": "Fields to change, e.g. max_records_per_action, max_emails_per_run, auto_allowed_max_risk"},
            "memory_policy": {"type": "object", "description": "Fields to change"},
            "retention_policy": {"type": "object", "description": "Fields to change, e.g. audit_days"},
        }
    ),
    "admin_toggle_domain": _obj(
        {"domain": _s("Pack name, e.g. finance"), "enabled": {"type": "boolean", "description": "On or off"}},
        ["domain", "enabled"],
    ),
    "admin_record_change_order": _obj(
        {
            "domain": _s("Pack name"),
            "reference": _s("Change-order reference, e.g. CO-2026-014"),
            "note": _s("Optional note"),
            "enable": {"type": "boolean", "description": "Also enable it (default true)"},
        },
        ["domain", "reference"],
    ),
    "admin_set_tool_permission": _obj(
        {
            "tool": _s("Tool name, e.g. email_send_message"),
            "role": _enum("Role whose permission changes", ["VIEWER", "DRAFTER", "APPROVER", "OPERATOR", "ADMIN"]),
            "permission": _enum(
                "What that role may do with the tool; default restores the registry value",
                ["DENIED", "DRAFT_ONLY", "APPROVE_ONLY", "EXECUTE_AFTER_APPROVAL", "ALLOWED", "default"],
            ),
        },
        ["tool", "role", "permission"],
    ),
    "admin_set_tool_mode": _obj(
        {
            "tool": _s("Tool name"),
            "mode": _enum(
                "Stricter mode for this tool (it can only tighten the workspace mode); default clears it",
                ["ADVISE", "DRAFT", "WAIT_FOR_APPROVAL", "default"],
            ),
        },
        ["tool", "mode"],
    ),
    "approvals_cancel": _obj(
        {
            "action_id": _s("Action id, e.g. act_…"),
            "match": _s("Words from the action's title, when the id is not known"),
            "comment": _s("Optional reason"),
        }
    ),
    "finance_list_invoices": _obj(
        {
            "customer": _s("Customer / company name"),
            "company": _s("Alias for customer"),
            "limit": _i("Max rows"),
        }
    ),
    "brand_get_voice": _obj({"query": _s("Optional search")}),
    "legal_find_clause": _obj({"query": _s("Clause search")}, ["query"]),
    "legal_flag_for_counsel": _obj(
        {
            "question": _s("Legal question"),
            "context": _s("Background"),
            "urgency": _s("normal|high"),
        },
        ["question"],
    ),
    "n8n_trigger_webhook": _obj(
        {
            "event": _s("Event name"),
            "workflow": _s("Optional workflow id"),
            "payload": {"type": "object", "description": "Event payload"},
        },
        ["event"],
    ),
}


def schema_for_tool(name: str) -> dict[str, Any]:
    """JSON Schema exposed to the model for one tool."""
    return TOOL_PARAMETER_SCHEMAS.get(name, OPEN_OBJECT_SCHEMA)
