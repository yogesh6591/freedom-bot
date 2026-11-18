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


#: An email address (the platform's own check, as in freedombot/admin_ops.py). A name such as "Raj" never matches.
EMAIL_PATTERN = r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$"


def _emails(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": f"{description}: email addresses only, never names (look them up first)",
        "items": {"type": "string", "pattern": EMAIL_PATTERN},
    }


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


#: Column names the query tool accepts, for the model. Keep in step with app/outreach/datasets.py.
_PEOPLE_COLUMNS = (
    "executive_name, email, company, executive_title, website, linkedin_url, revenue_band, headcount_band, source, "
    "fit_note, added_at, status, emails_sent, emails_queued, last_emailed_at, outreach_status, "
    "assessment_page_opened_at, assessment_status, assessment_started_at, assessment_completed_at, qualification, "
    "journey_stage, booking_status, meeting_start, consultation_show, checkout_offer_amount, checkout_offered_at, "
    "purchase_outcome, amount_paid, purchased_at, handoff_status"
)
_EMAIL_COLUMNS = "person, to_email, company, subject, status, kind, queued_at, sent_at, error"
_STATUS_VALUES = (
    "not_emailed, email_queued, emailed, opened_page, started_assessment, finished_assessment, booked, "
    "booking_cancelled, unsubscribed, bounced"
)
#: Journey setting names, for the model. Keep in step with app/outreach/journey_tools.py.
_JOURNEY_SETTINGS = (
    "demo_enabled, demo_require_watch, demo_video_url (a YouTube, Vimeo or direct https video link), demo_title, demo_description, value_enabled, value_heading, "
    "value_investment_text, budget_question, budget_label_ready, budget_label_unclear, budget_label_not_ready, "
    "value_continue_button_text, calendar_enabled, booking_url, after_booking_video_enabled, "
    "after_booking_video_url (a YouTube, Vimeo or direct https video link), after_booking_video_heading, after_booking_video_intro, after_booking_video_script, after_booking_prep_heading, "
    "after_booking_prep_body, after_booking_prep_note"
)


#: Fallback when a tool has no dedicated schema: allow any named fields.
OPEN_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


TOOL_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "synced_search": _obj(
        {
            "query": _s("Words to look for, e.g. a sender, company or topic (optional: empty lists the latest)"),
            "sources": {
                "type": "array",
                "description": "Only these sources (optional; default all)",
                "items": {"type": "string", "enum": ["gmail", "calendar", "youtube", "hubspot"]},
            },
            "since": _s("Only records on or after this ISO date or date-time (optional)"),
            "limit": _i("Max results (default 20, at most 50)"),
        }
    ),
    "memory_search": _obj(
        {
            "query": _s("Search text"),
            "categories": _arr("Optional FACT|SOP|DECISION|CORRECTION|DOCUMENT filters (DOCUMENT = uploaded knowledge-base files)"),
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
            "access_area": _s(
                "Who may see it: empty/everyone for the whole workspace; or finance, hr, legal, ops, sales, exec, salary"
            ),
        },
        ["title", "content"],
    ),
    "memory_add_sop": _obj(
        {
            "title": _s("SOP title"),
            "content": _s("Procedure text"),
            "key": _s("Stable lookup key"),
            "tags": _arr("Optional tags"),
            "access_area": _s(
                "Who may see it: empty/everyone for the whole workspace; or finance, hr, legal, ops, sales, exec, salary"
            ),
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
            "access_area": _s(
                "Who may see it: empty/everyone for the whole workspace; or finance, hr, legal, ops, sales, exec, salary"
            ),
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
            "category": _s("technical (errors, login, integrations, wrong AI answers) or business (billing, "
                           "subscription, onboarding, operational or client requests)"),
        },
        ["subject"],
    ),
    "schedule_create": _obj(
        {
            "title": _s("Short name for the task, e.g. 'Weekly Facebook tip'"),
            "instruction": _s("What to do each time, in the user's words but without the timing, e.g. 'Write "
                              "a short AI tip and post it on our Facebook Page'"),
            "frequency": _enum("How often: daily, weekly (chosen weekdays), or interval (every N minutes or "
                               "hours)", ["daily", "weekly", "interval"]),
            "time": _s("Time of day, e.g. 09:00 or 6:30 PM. Needed for daily and weekly; for interval only "
                       "when the user gives a start time"),
            "every_minutes": {"type": "integer", "minimum": 5,
                              "description": "For interval: minutes between runs, at least 5 (every 5 minutes -> 5, "
                                             "every 50 minutes -> 50, every 2 hours -> 120)"},
            "weekdays": {"type": "array", "description": "For weekly: the days it runs",
                         "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}},
            "timezone": _s("IANA timezone, e.g. Asia/Kolkata (optional; default the workspace's)"),
        },
        ["instruction", "frequency"],
    ),
    "platform_create_client": _obj(
        {
            "company_name": _s("The new client's company name"),
            "admin_email": _s("Email of the client's first admin, from the user's message"),
            "admin_name": _s("Optional: the admin's name"),
            "workspace_id": _s("Optional: workspace identifier (lowercase, used to sign in); made from the name if empty"),
            "bought": {"type": "array", "description": "The domains the client bought, exactly as the user said. If the user has not said, leave it empty: the call is refused with the list of domains to ask about. Never guess",
                       "items": {"type": "string", "enum": ["general", "operations", "sales", "intake", "planning",
                                                            "strategy", "finance", "brand", "legal"]}},
        },
        ["company_name", "admin_email"],
    ),
    "support_queue": _obj(
        {"show": _s("open (default) | triage | done | all")}
    ),
    "support_client_records": _obj(
        {"client": _s("The client's company name or workspace identifier, e.g. BrightPath")}, ["client"]
    ),
    "platform_overview": _obj(
        {"client": _s("Optional: one client's company name or workspace identifier, to list its members too")}
    ),
    "schedule_list": _obj(
        {
            "everyone": {"type": "boolean", "description": "Admins only: every person's schedules (default false)"},
            "include_ended": {"type": "boolean", "description": "Also show stopped ones (default false)"},
        }
    ),
    "schedule_update": _obj(
        {
            "schedule_id": _s("Schedule id (sch_...) from schedule_list or schedule_create"),
            "title": _s("New short name, only if it changes"),
            "instruction": _s("New instruction (what to do each time, without the timing), only if it changes"),
            "frequency": _enum("New frequency, only if it changes", ["daily", "weekly", "interval"]),
            "time": _s("New time of day, e.g. 09:00 or 6:30 PM, only if it changes"),
            "every_minutes": {"type": "integer", "minimum": 5,
                              "description": "For interval: new minutes between runs, at least 5 (every 2 hours -> 120)"},
            "weekdays": {"type": "array", "description": "For weekly: the new days it runs",
                         "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}},
            "timezone": _s("New IANA timezone, e.g. Asia/Kolkata, only if it changes"),
        },
        ["schedule_id"],
    ),
    "schedule_cancel": _obj(
        {"schedule_id": _s("Schedule id (sch_...) from schedule_list or schedule_create")},
        ["schedule_id"],
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
            "save_to": {"type": "string", "enum": ["freedombot", "hubspot", "google_drive"],
                        "description": "Only when the user names a place for this record: freedombot (our own "
                                       "database), hubspot, or google_drive. Leave out to use the user's usual place."},
        },
        ["first_name"],
    ),
    "crm_create_company": _obj(
        {
            "name": _s("Company name, e.g. 'QA NovaTech Solutions'"),
            "domain": _s("Web domain, e.g. novatech.com (optional)"),
            "industry": _s("Industry, e.g. Information Technology (optional)"),
            "size": _s("Company size, e.g. 11-50 (optional)"),
            "description": _s("One or two lines about the company (optional)"),
            "save_to": {"type": "string", "enum": ["freedombot", "hubspot", "google_drive"],
                        "description": "Only when the user names a place for this record: freedombot (our own "
                                       "database), hubspot, or google_drive. Leave out to use the user's usual place."},
        },
        ["name"],
    ),
    "crm_create_deal": _obj(
        {
            "name": _s("Deal name, e.g. 'NovaTech AI Automation Project'"),
            "amount": {"type": "number", "description": "Deal value as a number, e.g. 25000 (no currency sign)"},
            "stage": _s("Pipeline stage, e.g. new, qualified, proposal, negotiation, won, lost (default new)"),
            "close_date": _s("Expected close date as YYYY-MM-DD (optional)"),
            "company_id": _s("Company id from crm_search_companies or crm_create_company (optional)"),
            "contact_id": _s("Contact id from crm_search_contacts or crm_create_contact (optional)"),
            "currency": _s("Three-letter currency code (default USD)"),
            "save_to": {"type": "string", "enum": ["freedombot", "hubspot", "google_drive"],
                        "description": "Only when the user names a place for this record: freedombot (our own "
                                       "database), hubspot, or google_drive. Leave out to use the user's usual place."},
        },
        ["name", "amount"],
    ),
    "crm_get_storage": _obj({}),
    "crm_set_storage": _obj(
        {
            "destination": {"type": "string", "enum": ["freedombot", "hubspot", "google_drive", "auto"],
                            "description": "Where new CRM records go: freedombot (our own database), hubspot, "
                                           "google_drive (a FreedomBot CRM sheet in Drive), or auto (HubSpot when "
                                           "connected, otherwise FreedomBot)"},
        },
        ["destination"],
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
            "recipients": _emails("To"),
            "subject": _s("Subject"),
            "body": _s("Body"),
            "cc": _emails("Cc"),
        },
        ["recipients", "subject", "body"],
    ),
    "email_send_message": _obj(
        {
            "recipients": _emails("To"),
            "subject": _s("Subject"),
            "body": _s("Body"),
            "cc": _emails("Cc"),
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
    "youtube_transcript": _obj(
        {
            "video": _s("YouTube video id (from youtube_my_channel, youtube_search_videos) or a YouTube link from the user"),
            "language": _s("Preferred caption language code, e.g. en or hi (optional)"),
        },
        ["video"],
    ),
    "integration_find_action": _obj(
        {
            "query": _s("What the action should do, in plain words, e.g. 'reply to a youtube comment'"),
            "app": _s("Only this connected app, by its slug: gmail, googlecalendar, googledrive, googlesheets, "
                      "googledocs, youtube, hubspot or facebook "
                      "(optional; default every connected app)"),
            "limit": _i("Max actions (default 8, at most 15)"),
        },
        ["query"],
    ),
    "integration_read_action": _obj(
        {
            "action": _s("The action_id exactly as integration_find_action returned it, e.g. YOUTUBE_LIST_COMMENTS"),
            "arguments": {"type": "object", "description": "The action's arguments, named as integration_find_action listed them (read-only actions only)"},
        },
        ["action", "arguments"],
    ),
    "integration_write_action": _obj(
        {
            "action": _s("The action_id exactly as integration_find_action returned it, e.g. GMAIL_ADD_LABEL_TO_EMAIL"),
            "arguments": {"type": "object", "description": "The action's arguments, named as integration_find_action listed them (create or update actions only)"},
        },
        ["action", "arguments"],
    ),
    "integration_destructive_action": _obj(
        {
            "action": _s("The action_id exactly as integration_find_action returned it, e.g. YOUTUBE_CREATE_COMMENT_REPLY"),
            "arguments": {"type": "object", "description": "The action's arguments, named as integration_find_action listed them (delete, send or publish actions only)"},
        },
        ["action", "arguments"],
    ),
    "web_search": _obj(
        {
            "query": _s("What to search the public web for. Never include confidential company data"),
            "limit": _i("Max results (default 6, at most 10)"),
        },
        ["query"],
    ),
    "web_read": _obj({"url": _s("Full public http(s) address, from web_search results or the user's message")}, ["url"]),
    "attachment_read": _obj(
        {
            "attachment_id": _s("attachment_id from the Attached files list"),
            "query": _s("Optional words to find in a long file; returns only the matching parts or rows"),
            "start": _i("Optional character (text) or row (table) to continue from: next_start of the last read"),
        },
        ["attachment_id"],
    ),
    "memory_save_attachment": _obj(
        {
            "attachment_id": _s("attachment_id from the Attached files list"),
            "title": _s("Optional title in company memory (defaults to the file name)"),
            "access_area": _s(
                "Who may see it: leave empty or everyone for the whole workspace; "
                "or finance, hr, legal, ops, sales, exec, salary when the user limits access"
            ),
        },
        ["attachment_id"],
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
            "role": _s("The person's one role: advise | draft | approve | auto. draft when the user did not say"),
            "data_access": _arr("Extra departments they work in: general, sales, planning, finance, legal, ops, intake, strategy, brand, hr. CRM, Gmail and Calendar come with every role"),
            "job_role": _s("Their department: GENERAL | SALES | PLANNING | FINANCE | LEGAL | OPERATIONS | INTAKE | STRATEGY | BRAND | HR; or EMPLOYEE (view only) or ADMIN"),
            "roles": _arr("Only for odd cases: VIEWER | DRAFTER | APPROVER | OPERATOR | ADMIN"),
        },
        ["email"],
    ),
    "admin_set_user_roles": _obj(
        {
            "user": _s("Email or name"),
            "job_role": _s("Department: GENERAL | SALES | PLANNING | FINANCE | LEGAL | OPERATIONS | INTAKE | STRATEGY | BRAND | HR; or EMPLOYEE (view only) or ADMIN"),
            "roles": _arr("The full new set of roles, when no job_role fits"),
        },
        ["user"],
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
    "admin_set_user_mode": _obj(
        {"user": _s("Email or name"), "role": _s("advise | draft | approve | auto")},
        ["user", "role"],
    ),
    "admin_set_user_status": _obj(
        {"user": _s("Email or name"), "status": _s("ACTIVE | SUSPENDED")}, ["user", "status"]
    ),
    "admin_reset_password": _obj({"user": _s("Email or name")}, ["user"]),
    "admin_update_settings": _obj(
        {
            "default_execution_mode": _s("ADVISE | DRAFT | WAIT_FOR_APPROVAL | AUTO_WITHIN_SCOPE"),
            "order_dpa_allows": _arr("Full list of allowed regulated categories: phi, card, child, biometrics, ssn, licensed_advice"),
            "llm_baa_covered": {"type": "boolean", "description": "Model vendor has a signed BAA"},
            "approval_policy": {"type": "object", "description": "Fields to change, e.g. allow_self_approval, approver_role, expires_after_hours"},
            "risk_policy": {"type": "object", "description": "Fields to change, e.g. max_records_per_action, max_emails_per_run, auto_allowed_max_risk"},
            "memory_policy": {"type": "object", "description": "Fields to change"},
            "retention_policy": {"type": "object", "description": "Fields to change, e.g. audit_days"},
            "timezone": _s("Workspace timezone as an IANA name, e.g. Asia/Kolkata, America/New_York"),
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
    "show_sent_emails": _obj({
        "who": _s("Optional name or email; empty = latest across everyone"),
        "limit": _i("How many rows (default 10, max 30)"),
    }),
    "choose_recipients": _obj({
        "people": {
            "type": "array", "description": "Each person by saved name or by typed email",
            "items": {"type": "object", "properties": {
                "email": {"type": "string", "description": "Typed email address, if the user gave one"},
                "name": _s("Name as the user wrote it"),
                "company": _s("Company, only if the user stated it"),
            }, "additionalProperties": False},
        },
        "mode": _enum("replace (default), add or remove", ["replace", "add", "remove"]),
    }, ["people"]),
    "propose_email": _obj({
        "subject": _s("Subject; use {{firstName}} / {{companyName}} blanks, never a real name"),
        "body": _s("Body; blanks for people's details; keep {{assessment_url}} where the link goes"),
        "change_evidence": _s("The user's own words asking for the change, when they had edited the draft"),
        "per_person": {
            "type": "array",
            "description": "Only when different people should get different wording: one item per person. People not listed get the shared subject and body.",
            "items": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "That person's email"},
                    "subject": _s("Subject for this person; blanks, never a real name"),
                    "body": _s("Body for this person; blanks; keep {{assessment_url}} where the link goes"),
                },
                "required": ["email", "subject", "body"],
                "additionalProperties": False,
            },
        },
        "new_thread": {"type": "boolean", "description": "True only when the user wants a brand-new email instead of a follow-up in the earlier conversation. Leave out otherwise."},
        "same_for_all": {"type": "boolean", "description": "True ONLY when the user clearly wants identical text for everyone (they said same, identical, one email for all). Words like different, separate, individual, own, unique or each mean per_person instead. If the user did not say, use per_person."},
    }, []),
    "set_person_details": _obj({
        "email": _s("One chosen recipient's email"),
        "first_name": _s("First name"), "last_name": _s("Last name"), "company": _s("Company"),
        "evidence": _s("The user's own words giving this detail"),
    }, ["email", "evidence"]),
    "send_approved": _obj({"approval_evidence": _s("The user's own words approving, copied from their latest message")},
                          ["approval_evidence"]),
    "find_people": _obj({
        "stage": _arr("Where they are now, any of: new, not_started, in_progress, completed, qualified, proof, demo, value, calendar, unqualified, booked, payment_pending (offered, not paid), purchased (paid). Leave out for any stage."),
        "order": _enum("Only when the user says newest, latest, oldest or first added: newest or oldest, by when the person was added. Use with count. Otherwise leave it out, and the best-scored people come first.", ["newest", "oldest"]),
        "count": _i("How many to take after the other filters. With order and no count, 1."),
        "emailed": _enum("yes: already emailed, no: never emailed, any (default)", ["yes", "no", "any"]),
        "inactive_days": _i("Only people with no activity or email for at least this many days"),
        "text": _s("A word in the name, company, email or industry"),
        "place": _s("City, state or region. Only works when places are saved; the tool says if they are not"),
        "include_recent": {"type": "boolean", "description": "Also include people emailed in the last 2 days. Only when the user asks"},
    }, []),
    "show_email_template": _obj({}),
    "update_email_template": _obj({
        "first_subject": _s("New first email subject. Leave out to keep it as it is."),
        "first_body": _s("New first email body, the whole text with every {{blank}} kept. Leave out to keep it."),
        "stage": _s("Which stage email to change: not_started, in_progress, qualified, proof, demo, value, calendar, payment_pending or unqualified. Use with stage_body."),
        "stage_body": _s("New body for that stage, the whole text with every {{blank}} kept ({{missingTopics}} stays in in_progress, {{next_step_url}} in the later-step and payment emails)."),
        "change_evidence": _s("The user's own words asking for the change, copied from their latest message"),
        "remove_placeholders": _arr("Blank names the user clearly asked to remove, for example companyName. Usually empty."),
    }, ["change_evidence"]),
    "show_followup_schedule": _obj({}),
    "update_followup_schedule": _obj({
        "enabled": {"type": "boolean", "description": "true to switch on, false to switch off. With a stage it is that stage only; without a stage it is the whole automatic follow-up. Leave out to keep it."},
        "stage": {"type": "string", "enum": ["not_started", "in_progress", "qualified", "proof", "demo", "value", "calendar", "payment_pending"], "description": "Change only this stage: not_started (emailed, has not started the assessment), in_progress (started, not finished), qualified (finished, good fit), proof (looking at examples), demo, value (value summary), calendar (ready to book), payment_pending (payment offered). Leave out to change every stage."},
        "days": _i("Days after the person's last email or after they reached the stage, 1 to 30. Leave out to keep it."),
        "hour": _i("Hour of the day the follow-up goes out, Chicago time, 0 to 23 (6 pm is 18, 9 am is 9). Leave out to keep it."),
        "max_followups": _i("Most automatic follow-ups per person at that stage, 1 to 10. Leave out to keep it."),
        "change_evidence": _s("The user's own words asking for the change, copied from their latest message"),
    }, ["change_evidence"]),
    "send_from_template": _obj({
        "people": {
            "type": "array",
            "description": "Who to email by name or typed address. Leave out when using audience or filters.",
            "items": _obj({
                "email": _s("Typed email address"),
                "name": _s("Saved person's name"),
                "company": _s("Company, only if the user wrote it"),
            }, []),
        },
        "audience": _enum("Everyone saved: all, or only people not emailed yet. Leave out when using filters or names.",
                          ["all", "not_emailed_yet"]),
        "stage": _arr("Where they are now, any of: new, not_started, in_progress, completed, qualified, proof, demo, value, calendar, unqualified, booked, payment_pending (offered, not paid), purchased (paid). Leave out for any stage."),
        "order": _enum("Only when the user says newest, latest, oldest or first added: newest or oldest, by when the person was added. Use with count. Otherwise leave it out, and the best-scored people come first.", ["newest", "oldest"]),
        "count": _i("How many to take after the other filters. With order and no count, 1."),
        "emailed": _enum("yes: already emailed, no: never emailed, any (default)", ["yes", "no", "any"]),
        "inactive_days": _i("Only people with no activity or email for at least this many days"),
        "text": _s("A word in the name, company, email or industry"),
        "place": _s("City, state or region. Only works when places are saved; the tool says if they are not"),
        "include_recent": {"type": "boolean", "description": "Also include people emailed in the last 2 days. Only when the user asks"},
        "subject": _s("Subject for first emails, only when the user gave a topic or wording. Blanks allowed. Leave out to use the saved emails."),
        "body": _s("Email body, only when the user gave a topic or wording. Keep {{firstName}} and {{assessment_url}}, and end with Best, then {{senderName}}. Leave out to use the saved emails."),
        "approval_evidence": _s("Only if the tool asked for a yes: the user's words saying yes"),
    }, []),
    "cancel_sending": _obj({}),
    # ------------------------------------------------------------------ outreach: records
    "query_records": _obj({
        "dataset": _enum("What to look at: people (default), emails (sent or waiting), bookings, payments",
                         ["people", "emails", "bookings", "payments"]),
        "filters": {
            "type": "array",
            "description": "Conditions that must all hold. Leave out to match everything.",
            "items": _obj({
                "field": _s(
                    "Column name, or 'search' to look across name, email and company (forgives typos). "
                    f"people/bookings/payments columns: {_PEOPLE_COLUMNS}. emails columns: {_EMAIL_COLUMNS}."),
                "op": _enum("equals (default), not_equals, contains (forgives typos in names, emails and companies), "
                            "gt, gte, lt, lte, between (needs value2), is_empty, not_empty",
                            ["equals", "not_equals", "contains", "gt", "gte", "lt", "lte", "between", "is_empty", "not_empty"]),
                "value": _s(f"The value. Dates: YYYY-MM-DD (the whole day, Chicago time), a full date and time, today, "
                            f"yesterday or tomorrow. status is one of: {_STATUS_VALUES}"),
                "value2": _s("Upper end for between"),
            }, ["field"]),
        },
        "columns": _arr("Up to 8 columns to show. Leave out for a sensible default."),
        "sort_by": _s("Column to sort by (default: newest first)"),
        "sort_dir": _enum("asc or desc", ["asc", "desc"]),
        "group_by": _s("Count rows per value of this column instead of listing them (for example status or booking_status)"),
        "limit": _i("Rows to show (default 20, max 50)"),
        "offset": _i("Rows to skip, to see the next page"),
        "count_only": {"type": "boolean", "description": "Only return how many match"},
    }),
    "show_prospect": _obj({"who": _s("Name or email; leave empty to use the person already chosen")}),
    "add_prospects": _obj({
        "people": {
            "type": "array", "description": "Each new person (up to 100). Each needs a name and an email.",
            "items": _obj({
                "executive_name": _s("Full name (or give first_name and last_name)"),
                "first_name": _s("First name"), "last_name": _s("Last name"),
                "email": _s("Email address"), "company": _s("Company"), "executive_title": _s("Job title"),
                "website": _s("Company website"), "linkedin_url": _s("LinkedIn profile URL"),
                "revenue_band": _s("Revenue band, as written"), "headcount_band": _s("Headcount band, as written"),
                "source": _s("Where this person came from"), "fit_note": _s("Free note on why they fit"),
            }),
        },
    }, ["people"]),
    "research_prospects": _obj({
        "people": {"type": "array", "items": {"type": "string"}, "description": "Names or emails of the people to research"},
        "where": {
            "type": "array",
            "description": "Instead of people: a filter that picks the group (same shape as query_records filters).",
            "items": _obj({"field": _s("Column name or 'search'"), "op": _s("equals, contains, ..."), "value": _s("Value"),
                           "value2": _s("Upper end for between")}, ["field"]),
        },
    }),
    "show_research": _obj({}),
    "find_new_prospects": _obj({
        "count": _i("How many new people to find (1 to 5, default 5)"),
        "focus": _s("Kind of firm to look for, only if the user named one; leave empty to use the targeting rules"),
        "state": _s("US state (name or two-letter code), only if the user named one"),
        "employees_min": _i("Smallest headcount, only if the user named one"),
        "employees_max": _i("Largest headcount, only if the user named one"),
    }),
    "update_prospects": _obj({
        "changes": _obj({
            "executive_name": _s("Full name"), "first_name": _s("First name"), "last_name": _s("Last name"),
            "email": _s("Email address"), "company": _s("Company"), "executive_title": _s("Job title"),
            "website": _s("Company website"), "linkedin_url": _s("LinkedIn profile URL"),
            "revenue_band": _s("Revenue band"), "headcount_band": _s("Headcount band"),
            "source": _s("Source"), "fit_note": _s("Fit note"),
        }),
        "people": _arr("Names or emails of the people to change (one person changes at once; more ask yes first)"),
        "where": {
            "type": "array",
            "description": "Instead of people: a filter that picks the group (same shape as query_records filters). Needs at least one condition.",
            "items": _obj({"field": _s("Column name or 'search'"), "op": _s("equals, contains, ..."), "value": _s("Value"),
                           "value2": _s("Upper end for between")}, ["field"]),
        },
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }, ["changes"]),
    "delete_prospects": _obj({
        "people": _arr("Names or emails of the people to delete (up to 25)"),
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }, ["people"]),
    "refuse_bulk_change": _obj({}),
    # ------------------------------------------------------------------ outreach: assessments
    "show_assessment": _obj({
        "who": _s("Name or email of the person; leave empty to use the person already chosen"),
        "details": {"type": "boolean", "description": "True only when the user asks for the details or the full report"},
        "include_brief": {"type": "boolean", "description": "True when the user asks for the pre-call brief; adds ready/not-ready and the link to the report"},
    }, []),
    "set_qualification": _obj({
        "who": _s("Name or email of the person; leave empty to use the person already chosen"),
        "result": _enum("The new result: qualified (approve) or unqualified (unapprove). There is no pending option here.", ["qualified", "unqualified"]),
    }, ["result"]),
    "rerun_qualification": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }),
    "reset_journey": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }),
    "record_consultation": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "outcome": _enum("Did they attend?", ["showed", "no_show", "not_recorded"]),
    }, ["outcome"]),
    "set_handoff_status": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "status": _enum("Delivery handoff stage", ["not_started", "ready", "handed_off"]),
    }, ["status"]),
    # ------------------------------------------------------------------ outreach: bookings
    "reschedule_booking": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "start": _s("New start time in Chicago time as YYYY-MM-DD HH:MM (24-hour), for example 2026-11-05 14:30"),
    }, ["start"]),
    "cancel_booking": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }),
    # ------------------------------------------------------------------ outreach: payments
    "create_payment_offer": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "amount": _f("The one-time amount in US dollars, for example 5000"),
    }, ["amount"]),
    "send_payment_link": _obj({
        "who": _s("Name or email; leave empty to use the person already chosen"),
        "amount": _f("One-time amount in US dollars when the user named it and no offer exists yet, for example 700"),
        "approval_evidence": _s("Unused; keep empty. Approving a card uses send_approved."),
    }),
    # ------------------------------------------------------------------ outreach: journey content
    "show_journey_settings": _obj({}),
    "update_journey_settings": _obj({
        "changes": {
            "type": "object",
            "description": f"Setting name -> new value. Settings: {_JOURNEY_SETTINGS}. On/off settings take true or false; empty text clears it.",
            "additionalProperties": True,
        },
    }, ["changes"]),
    "edit_case_study": _obj({
        "action": _enum("What to do", ["add", "edit", "go_live", "make_draft", "delete"]),
        "which": _s("Title of the case study (not needed when adding)"),
        "title": _s("Title"), "client_display_name": _s("Client name shown to prospects"),
        "problem": _s("The problem"), "change": _s("What changed"), "measurable_result": _s("The measurable result"),
        "display_order": _i("Order in the list, lowest first"),
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }, ["action"]),
    "edit_video_link": _obj({
        "action": _enum("What to do", ["add", "edit", "go_live", "make_draft", "delete"]),
        "which": _s("Title of the video link (not needed when adding)"),
        "title": _s("Title"), "url": _s("YouTube link"), "display_order": _i("Order in the list, lowest first"),
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }, ["action"]),
    "publish_journey": _obj({
        "approval_evidence": _s("On the second call: the user's yes, copied from their latest message"),
    }),
}


def schema_for_tool(name: str) -> dict[str, Any]:
    """JSON Schema exposed to the model for one tool."""
    return TOOL_PARAMETER_SCHEMAS.get(name, OPEN_OBJECT_SCHEMA)
