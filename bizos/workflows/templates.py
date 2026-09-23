"""
Workflow Templates
==================

The three Phase 1 playbooks (§13), declared as reusable templates that each
client customizes: steps, schedule, connector, execution mode, approval rules and
recipients live in the workspace's ``workflow_configs`` row, not in the code.

The steps themselves are ordinary Python that calls
:func:`bizos.tools.base.guarded_call`, so **every write a workflow performs goes
through the same policy engine as a chat request**. A workflow is not a privileged
path — running under AUTO_WITHIN_SCOPE it still hits the approval queue whenever a
step exceeds the client's limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    name: str
    description: str
    domain: str
    step_names: tuple[str, ...]
    #: Cron expression for scheduled templates; ``None`` for on-demand ones.
    cron: Optional[str] = None
    enabled_by_default: bool = True
    #: Inputs the caller must supply for a manual run.
    required_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "steps": list(self.step_names),
            "cron": self.cron,
            "enabled_by_default": self.enabled_by_default,
            "required_inputs": list(self.required_inputs),
        }


LEAD_INTAKE = WorkflowTemplate(
    id="lead_intake",
    name="Lead Intake",
    description=(
        "Takes a new inbound lead, enriches it from permitted sources, checks for a duplicate, "
        "classifies it, creates or updates the CRM record, and drafts a response — with every "
        "write routed through the policy engine so the workspace's mode decides whether the "
        "response is drafted, queued for approval, or sent."
    ),
    domain="intake",
    step_names=(
        "enrich_lead",
        "check_duplicate",
        "classify_lead",
        "upsert_crm_record",
        "draft_response",
    ),
    required_inputs=("email",),
)

MEETING_PREPARATION = WorkflowTemplate(
    id="meeting_preparation",
    name="Meeting Preparation",
    description=(
        "For an upcoming calendar event: find the attendees, pull their CRM records, search "
        "previous email, summarize open tasks, and produce a meeting brief."
    ),
    domain="planning",
    step_names=(
        "find_event",
        "resolve_attendees",
        "gather_crm_context",
        "gather_email_context",
        "summarize_open_items",
        "compose_brief",
    ),
    required_inputs=(),
)

WEEKLY_SALES_SUMMARY = WorkflowTemplate(
    id="weekly_sales_summary",
    name="Weekly Sales Summary",
    description=(
        "Reads the CRM pipeline, analyses stage distribution and week-over-week movement, "
        "writes a summary to organizational memory, and notifies the configured recipients."
    ),
    domain="sales",
    cron="0 8 * * 1",  # Mondays 08:00
    step_names=(
        "read_pipeline",
        "analyze_pipeline",
        "compare_changes",
        "compose_summary",
        "save_report",
        "notify_recipients",
    ),
)

WORKFLOW_TEMPLATES: tuple[WorkflowTemplate, ...] = (
    LEAD_INTAKE,
    MEETING_PREPARATION,
    WEEKLY_SALES_SUMMARY,
)

TEMPLATES_BY_ID = {t.id: t for t in WORKFLOW_TEMPLATES}
