"""
Business Playbooks
==================

The three Phase 1 workflows (§13). Each step is a plain function that reads and
writes through :func:`bizos.tools.base.guarded_call`, so the workspace's execution
mode decides at every step whether a write happens, becomes a draft, or enters the
approval queue.

That is the point worth noticing: none of these functions contains an ``if mode ==``
branch. The behaviour difference between Advise, Draft, Approval and Auto falls out
of the policy engine, so a playbook cannot accidentally implement a fifth mode.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from bizos.connectors.registry import get_connector
from bizos.review import store as review_store
from bizos.tools.base import RunScope, ToolOutcome, guarded_call
from bizos.types import MemoryCategory, PolicyEffect, SourceType
from bizos.util.timeutil import utcnow
from bizos.workflows.runner import NeedsHumanInput, StepResult, WorkflowState


def _step_from_outcome(name: str, outcome: ToolOutcome, **output: Any) -> StepResult:
    """Turn a guarded call into a step result, treating "queued for approval" as a
    legitimate outcome rather than a failure."""
    awaiting = outcome.effect in (PolicyEffect.REQUIRE_APPROVAL, PolicyEffect.DRAFT_ONLY)
    return StepResult(
        name=name,
        ok=outcome.ok,
        summary=outcome.message,
        output={"effect": str(outcome.effect), "data": outcome.data, **output},
        action_id=outcome.action_id,
        awaiting_approval=awaiting,
        error=None if outcome.ok else outcome.message,
    )


# ---------------------------------------------------------------------------
# 1. Lead Intake
# ---------------------------------------------------------------------------


def enrich_lead(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Gather what the workspace already knows about this lead.

    Enrichment is read-only and stays inside permitted sources — the CRM the
    client has connected and their own organizational memory. No external
    scraping, which would be an unreviewed data-collection path.
    """
    email = str(inputs.get("email", "")).strip().lower()
    if not email:
        return StepResult("enrich_lead", False, "no email supplied", error="email is required")

    company_domain = email.split("@", 1)[1] if "@" in email else ""
    companies = guarded_call(scope, "crm_search_companies", {"query": company_domain})
    sop = guarded_call(
        scope, "memory_search", {"query": "lead", "categories": ["SOP"], "limit": 3}
    )
    enriched = {
        "email": email,
        "company_domain": company_domain,
        "known_companies": companies.data or [],
        "applicable_sops": [s.get("title") for s in (sop.data or [])],
        "name": inputs.get("name", ""),
        "message": inputs.get("message", ""),
    }
    state.set("lead", enriched)
    return StepResult(
        "enrich_lead",
        True,
        f"enriched {email} against {len(enriched['known_companies'])} known company record(s)",
        output=enriched,
    )


def check_duplicate(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Never create a duplicate contact (§13 and the intake guardrail)."""
    lead = state.get("lead", {})
    crm = get_connector("crm", ctx=scope.ctx)
    result = crm.find_duplicate(lead.get("email", ""))
    state.set("duplicate", result.data)
    return StepResult(
        "check_duplicate",
        True,
        result.summary,
        output={"duplicate": result.data},
    )


def classify_lead(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Classify against the organization's recorded criteria.

    When the signals are too weak to classify confidently, the workflow raises a
    human review item and pauses rather than assigning a category at random.
    """
    lead = state.get("lead", {})
    message = (lead.get("message") or "").lower()
    known = bool(lead.get("known_companies"))

    if any(word in message for word in ("pricing", "quote", "demo", "buy", "contract")):
        classification, confidence = "hot", 0.9
    elif known:
        classification, confidence = "warm", 0.75
    elif message:
        classification, confidence = "cold", 0.6
    else:
        # No message, no known company: there is genuinely not enough to go on.
        item = review_store.create(
            question=f"How should the lead {lead.get('email')} be classified?",
            context=(
                "The lead arrived with no message body and no matching company record, so there "
                "is not enough signal to classify it against the recorded criteria."
            ),
            choices=["hot", "warm", "cold", "discard"],
            recommended_option="cold",
            confidence=0.3,
            agent_id=scope.agent_id,
            workflow_id=scope.workflow_id,
            workflow_run_id=state.run_id,
            ctx=scope.ctx,
        )
        raise NeedsHumanInput(item.id, item.question)

    state.set("classification", classification)
    state.set("confidence", confidence)
    return StepResult(
        "classify_lead",
        True,
        f"classified as {classification} (confidence {confidence})",
        output={"classification": classification, "confidence": confidence},
    )


def upsert_crm_record(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Update the existing contact, or create one. Both go through policy."""
    lead = state.get("lead", {})
    duplicate = state.get("duplicate")
    classification = state.get("classification", "cold")

    if duplicate:
        outcome = guarded_call(
            scope,
            "crm_tag_lead",
            {
                "contact_id": duplicate["id"],
                "add_tags": [f"intake:{classification}"],
                "lifecycle": "qualified" if classification == "hot" else "lead",
            },
        )
        state.set("contact_id", duplicate["id"])
        return _step_from_outcome("upsert_crm_record", outcome, mode="update")

    name = (lead.get("name") or "").split(" ", 1)
    outcome = guarded_call(
        scope,
        "crm_create_contact",
        {
            "first_name": name[0] or lead.get("email", "").split("@")[0],
            "last_name": name[1] if len(name) > 1 else "",
            "email": lead.get("email"),
            "company": lead.get("company_domain"),
            "lifecycle": "qualified" if classification == "hot" else "lead",
            "tags": [f"intake:{classification}"],
        },
    )
    if outcome.ok and isinstance(outcome.data, dict):
        state.set("contact_id", outcome.data.get("id"))
    return _step_from_outcome("upsert_crm_record", outcome, mode="create")


def draft_response(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Compose the reply and hand it to the policy engine.

    This single call is what produces all four behaviours: denied in Advise,
    drafted in Draft, queued in Approval, sent in Auto if it clears the limits.
    """
    lead = state.get("lead", {})
    classification = state.get("classification", "cold")
    body = (
        f"Hi {lead.get('name') or 'there'},\n\n"
        "Thanks for getting in touch. I've logged your enquiry and someone from the team will "
        "follow up shortly.\n\nBest regards,\nThe team"
    )
    outcome = guarded_call(
        scope,
        "email_send_message",
        {
            "recipients": [lead.get("email")],
            "subject": "Thanks for reaching out",
            "body": body,
        },
    )
    state.set("response_effect", str(outcome.effect))
    return _step_from_outcome("draft_response", outcome, classification=classification)


LEAD_INTAKE_STEPS = [
    ("enrich_lead", enrich_lead),
    ("check_duplicate", check_duplicate),
    ("classify_lead", classify_lead),
    ("upsert_crm_record", upsert_crm_record),
    ("draft_response", draft_response),
]


# ---------------------------------------------------------------------------
# 2. Meeting Preparation
# ---------------------------------------------------------------------------


def find_event(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    now = utcnow()
    outcome = guarded_call(
        scope,
        "calendar_list_events",
        {"start": now.isoformat(), "end": (now + timedelta(days=7)).isoformat(), "limit": 10},
    )
    events = outcome.data or []
    wanted = inputs.get("event_id")
    event = next((e for e in events if e.get("id") == wanted), events[0] if events else None)
    if event is None:
        return StepResult("find_event", False, "no upcoming events found", error="no events")
    state.set("event", event)
    return StepResult("find_event", True, f"preparing for {event.get('title')!r}", output={"event": event})


def resolve_attendees(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    event = state.get("event", {})
    attendees = [a for a in (event.get("attendees") or []) if a]
    state.set("attendees", attendees)
    return StepResult(
        "resolve_attendees", True, f"{len(attendees)} attendee(s)", output={"attendees": attendees}
    )


def gather_crm_context(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    records = []
    for attendee in state.get("attendees", []):
        outcome = guarded_call(scope, "crm_get_contact", {"contact_id": attendee})
        if outcome.ok and outcome.data:
            records.append(outcome.data)
    state.set("crm_context", records)
    return StepResult(
        "gather_crm_context", True, f"{len(records)} CRM record(s)", output={"contacts": len(records)}
    )


def gather_email_context(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Search prior correspondence. Bodies arrive neutralized as untrusted data."""
    threads, findings = [], 0
    for attendee in state.get("attendees", []):
        outcome = guarded_call(scope, "email_search_threads", {"query": attendee, "limit": 5})
        findings += outcome.untrusted_findings
        threads.extend(outcome.data or [])
    state.set("email_context", threads)
    state.set("untrusted_findings", findings)
    summary = f"{len(threads)} prior thread(s)"
    if findings:
        summary += f"; {findings} instruction-shaped span(s) neutralized in retrieved content"
    return StepResult(
        "gather_email_context", True, summary, output={"threads": len(threads), "findings": findings}
    )


def summarize_open_items(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    deals = guarded_call(scope, "crm_search_deals", {"limit": 20})
    attendees = set(state.get("attendees", []))
    relevant = [
        d
        for d in (deals.data or [])
        if any(a.split("@")[-1] in str(d.get("name", "")).lower() for a in attendees)
    ] or (deals.data or [])[:3]
    state.set("open_items", relevant)
    return StepResult(
        "summarize_open_items", True, f"{len(relevant)} open item(s)", output={"open_items": len(relevant)}
    )


def compose_brief(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Assemble the brief. Deterministic — no model required."""
    event = state.get("event", {})
    lines = [
        f"# Meeting brief: {event.get('title', 'Untitled')}",
        f"When: {event.get('starts_at')}",
        f"Attendees: {', '.join(state.get('attendees', [])) or 'none listed'}",
        "",
        "## Who they are",
    ]
    for contact in state.get("crm_context", []):
        lines.append(
            f"- {contact.get('first_name','')} {contact.get('last_name','')} "
            f"({contact.get('title') or 'role unknown'}) at {contact.get('company') or 'unknown'} — "
            f"lifecycle {contact.get('lifecycle')}, {len(contact.get('notes') or [])} note(s)"
        )
    if not state.get("crm_context"):
        lines.append("- No CRM records found for these attendees.")

    lines += ["", "## Recent correspondence"]
    for thread in state.get("email_context", [])[:5]:
        lines.append(f"- {thread.get('subject')} (last {thread.get('sent_at')})")
    if not state.get("email_context"):
        lines.append("- No prior threads found.")

    lines += ["", "## Open items"]
    for item in state.get("open_items", []):
        lines.append(f"- {item.get('name')} — {item.get('stage')}, {item.get('amount')}")
    if not state.get("open_items"):
        lines.append("- Nothing open.")

    if state.get("untrusted_findings"):
        lines += [
            "",
            "## Security note",
            f"{state.get('untrusted_findings')} instruction-shaped span(s) were found and "
            "neutralized in retrieved correspondence. Treat that content as suspect.",
        ]

    brief = "\n".join(lines)
    state.set("brief", brief)
    return StepResult("compose_brief", True, f"brief composed ({len(brief)} chars)", output={"brief": brief})


MEETING_PREP_STEPS = [
    ("find_event", find_event),
    ("resolve_attendees", resolve_attendees),
    ("gather_crm_context", gather_crm_context),
    ("gather_email_context", gather_email_context),
    ("summarize_open_items", summarize_open_items),
    ("compose_brief", compose_brief),
]


# ---------------------------------------------------------------------------
# 3. Weekly Sales Summary
# ---------------------------------------------------------------------------


def read_pipeline(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    outcome = guarded_call(scope, "crm_pipeline_summary", {})
    if not outcome.ok:
        return StepResult("read_pipeline", False, outcome.message, error=outcome.message)
    state.set("pipeline", outcome.data)
    return StepResult("read_pipeline", True, outcome.message, output=outcome.data or {})


def analyze_pipeline(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    pipeline = state.get("pipeline", {}) or {}
    stages = pipeline.get("stages", [])
    total = pipeline.get("total_value", 0.0)
    largest = max(stages, key=lambda s: s["total"], default=None)
    analysis = {
        "total_value": total,
        "stage_count": len(stages),
        "largest_stage": largest["stage"] if largest else None,
        "largest_stage_value": largest["total"] if largest else 0.0,
        "concentration": round((largest["total"] / total) if largest and total else 0.0, 3),
    }
    state.set("analysis", analysis)
    return StepResult(
        "analyze_pipeline",
        True,
        f"largest stage {analysis['largest_stage']} holds "
        f"{analysis['concentration']:.0%} of pipeline value",
        output=analysis,
    )


def compare_changes(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Compare against the last saved report in organizational memory."""
    previous = guarded_call(
        scope, "memory_get_fact", {"category": "FACT", "key": "weekly_sales_snapshot"}
    )
    prior_value = None
    if previous.ok and previous.data:
        raw = (previous.data or {}).get("attributes", {}).get("total_value")
        prior_value = float(raw) if raw is not None else None

    current = state.get("analysis", {}).get("total_value", 0.0)
    delta = None if prior_value is None else round(current - prior_value, 2)
    state.set("delta", delta)
    state.set("prior_value", prior_value)
    summary = (
        f"pipeline moved {delta:+,.2f} since last report"
        if delta is not None
        else "no prior report to compare against"
    )
    return StepResult("compare_changes", True, summary, output={"delta": delta, "prior": prior_value})


def compose_summary(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    pipeline = state.get("pipeline", {}) or {}
    analysis = state.get("analysis", {})
    delta = state.get("delta")
    lines = [
        "# Weekly sales summary",
        f"Total open pipeline: {analysis.get('total_value', 0):,.2f}",
        f"Deals changed in the last 7 days: {pipeline.get('changed_last_7_days', 0)}",
    ]
    if delta is not None:
        lines.append(f"Change since last report: {delta:+,.2f}")
    lines += ["", "## By stage"]
    for stage in pipeline.get("stages", []):
        lines.append(f"- {stage['stage']}: {stage['deals']} deal(s), {stage['total']:,.2f}")
    lines += [
        "",
        f"Concentration: {analysis.get('concentration', 0):.0%} of value sits in "
        f"{analysis.get('largest_stage')}.",
    ]
    report = "\n".join(lines)
    state.set("report", report)
    return StepResult("compose_summary", True, "summary composed", output={"report": report})


def save_report(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Save to organizational memory so next week can compare against it.

    Written with ``source_type=WORKFLOW``, so retrieval ranks it below anything a
    person stated directly.
    """
    analysis = state.get("analysis", {})
    outcome = guarded_call(
        scope,
        "memory_add_fact",
        {
            "key": "weekly_sales_snapshot",
            "title": "Weekly sales snapshot",
            "content": state.get("report", ""),
            "source_type": str(SourceType.WORKFLOW),
            "domain": "sales",
            "attributes": {"total_value": analysis.get("total_value", 0.0)},
            "confidence": 0.95,
        },
    )
    return _step_from_outcome("save_report", outcome)


def notify_recipients(scope: RunScope, inputs: dict[str, Any], state: WorkflowState) -> StepResult:
    """Notify the configured recipients — through policy, like any other send."""
    from bizos.workflows.runner import get_config

    config = get_config(scope.workflow_id or "weekly_sales_summary", ctx=scope.ctx) or {}
    recipients = list(config.get("recipients") or inputs.get("recipients") or [])
    if not recipients:
        return StepResult(
            "notify_recipients",
            True,
            "no recipients configured; report saved to memory only",
            output={"recipients": []},
        )
    outcome = guarded_call(
        scope,
        "email_send_message",
        {
            "recipients": recipients,
            "subject": "Weekly sales summary",
            "body": state.get("report", ""),
        },
    )
    return _step_from_outcome("notify_recipients", outcome, recipients=recipients)


WEEKLY_SALES_STEPS = [
    ("read_pipeline", read_pipeline),
    ("analyze_pipeline", analyze_pipeline),
    ("compare_changes", compare_changes),
    ("compose_summary", compose_summary),
    ("save_report", save_report),
    ("notify_recipients", notify_recipients),
]


PLAYBOOKS: dict[str, list] = {
    "lead_intake": LEAD_INTAKE_STEPS,
    "meeting_preparation": MEETING_PREP_STEPS,
    "weekly_sales_summary": WEEKLY_SALES_STEPS,
}


def run_playbook(
    scope: RunScope, template: str, *, inputs: dict[str, Any] | None = None, trigger: str = "manual"
):
    """Run one playbook by template id."""
    from bizos.workflows.runner import execute
    from bizos.workflows.templates import TEMPLATES_BY_ID

    steps = PLAYBOOKS.get(template)
    if steps is None:
        raise KeyError(f"Unknown workflow template {template!r}")
    meta = TEMPLATES_BY_ID[template]
    scope.domain = meta.domain
    return execute(
        scope,
        workflow_id=template,
        template=template,
        steps=steps,
        inputs=inputs or {},
        trigger=trigger,
    )
