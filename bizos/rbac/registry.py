"""
Tool Registry
=============

The single, central declaration of every tool the platform can expose, with the
metadata the policy engine, the tool-surface builder and the Admin UI all read
(§7: "Prefer central configuration/database policy definitions").

Adding a capability means adding one :class:`ToolSpec` here and one implementation
in :mod:`bizos.tools`. There is no second place where a permission is decided —
a startup check (:func:`bizos.tools.registry_check.verify_registry`) fails if an
implementation exists without a spec, or a spec without an implementation.

Naming follows §19: narrow verbs (``crm_search_contacts``), never a general
executor. :func:`assert_no_generic_tools` enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Optional

from bizos.rbac import matrix
from bizos.types import DataClassification, RiskLevel, Role, ToolPermission, role_rank

#: Connector-ish grouping, used by the UI and by integration gating.
CATEGORY_INTERNAL = "internal"
CATEGORY_MEMORY = "memory"
CATEGORY_CRM = "crm"
CATEGORY_EMAIL = "email"
CATEGORY_CALENDAR = "calendar"
CATEGORY_STRATEGY = "strategy"
CATEGORY_FINANCE = "finance"
CATEGORY_BRAND = "brand"
CATEGORY_LEGAL = "legal"
CATEGORY_HR = "hr"
CATEGORY_N8N = "n8n"
CATEGORY_YOUTUBE = "youtube"
CATEGORY_ADMIN = "admin"
CATEGORY_CAMPAIGN = "campaign"


@dataclass(frozen=True)
class ToolSpec:
    """Everything the governance layer knows about one tool.

    ``write``/``external`` are the two facts the execution modes key off:
    ADVISE forbids every ``write``; DRAFT forbids every ``external`` write but
    permits drafting.
    """

    name: str
    title: str
    description: str
    category: str
    #: Which domain packs may expose this tool. Empty means "any enabled domain".
    domains: frozenset[str]
    #: Does invoking this change state anywhere?
    write: bool
    #: Does the change leave the customer's workspace (a real email, a real
    #: calendar, a vendor CRM)? Internal writes are `write and not external`.
    external: bool
    risk: RiskLevel
    classification: DataClassification
    permissions: Mapping[Role, ToolPermission]
    #: Approval is required whatever the mode or risk (§7 per-tool override).
    always_requires_approval: bool = False
    #: Executing this would constitute a decision reserved to a licensed human
    #: (§14). Never auto-executed, in any mode, under any client configuration.
    licensed_judgment: bool = False
    #: Touches protected health information — blocked unless ``allow_phi``.
    phi_sensitive: bool = False
    #: Touches payment-card data — blocked unless ``allow_card_data``.
    card_sensitive: bool = False
    #: Moves or commits money; subject to the financial limit.
    financial: bool = False
    #: The draft-producing counterpart, offered when a write is downgraded.
    draft_counterpart: Optional[str] = None
    #: The integration whose connection this tool needs, if any.
    integration: Optional[str] = None
    #: False for specs that are declared (so policy and UI know about them) but
    #: whose adapter is not part of Phase 1.
    implemented: bool = True
    #: A write that is *itself* the act of preparing something for review, or a
    #: change confined to the workspace's own record of what it has been told.
    #: These stay available in DRAFT mode, because blocking them would mean the
    #: agent could not produce the draft the mode exists to produce, nor record
    #: a correction the user just gave it. They never touch an external system.
    draft_safe: bool = False
    #: Acts on the approval queue itself (approve, reject, submit, cancel), so
    #: DRAFT and WAIT_FOR_APPROVAL must not queue it as another action. ADVISE
    #: still forbids it. The action store decides who may, and the queued action
    #: re-runs policy when it executes.
    governs_queue: bool = False
    #: Administration-page configuration (users, policies, packs, setup). The
    #: admin's own authority, as on the page, so DRAFT and WAIT_FOR_APPROVAL do
    #: not queue it; ADVISE still forbids it.
    admin_config: bool = False
    #: Free-form tags used by domain packs to select tools.
    tags: frozenset[str] = field(default_factory=frozenset)
    #: Restricted data area this tool reads or writes (FB-037). A caller without
    #: that area in their data scopes can neither see nor run it.
    data_area: Optional[str] = None
    #: FB-037 "can see a process" vs "can run it": the lowest role that may see
    #: this tool exists and what it does. Whether they may *run* it is the
    #: separate ``permissions`` matrix.
    view_min_role: Role = Role.VIEWER
    #: Set when a queued call could never run later (its state lives only in the chat
    #: turn). Whenever the policy check would queue it for approval or as a draft, the call
    #: is refused with this reason instead, so no dead approval card is created.
    not_queueable: Optional[str] = None

    def can_view(self, ctx) -> bool:
        return ctx.at_least(self.view_min_role) and ctx.can_see_area(self.data_area)

    def can_run(self, role: Role) -> bool:
        return self.permission_for(role) not in (ToolPermission.DENIED, ToolPermission.APPROVE_ONLY)

    @property
    def skips_mode_queue(self) -> bool:
        """DRAFT and WAIT_FOR_APPROVAL run this directly instead of queueing it."""
        return self.draft_safe or self.governs_queue or self.admin_config

    @property
    def internal_write(self) -> bool:
        """A write that stays inside the customer's own workspace."""
        return self.write and not self.external

    def permission_for(self, role: Role) -> ToolPermission:
        return self.permissions.get(role, ToolPermission.DENIED)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "domains": sorted(self.domains),
            "write": self.write,
            "external": self.external,
            "risk": str(self.risk),
            "classification": str(self.classification),
            "permissions": {str(r): str(p) for r, p in self.permissions.items()},
            "always_requires_approval": self.always_requires_approval,
            "licensed_judgment": self.licensed_judgment,
            "phi_sensitive": self.phi_sensitive,
            "card_sensitive": self.card_sensitive,
            "financial": self.financial,
            "draft_counterpart": self.draft_counterpart,
            "integration": self.integration,
            "implemented": self.implemented,
            "tags": sorted(self.tags),
            "data_area": self.data_area,
            "view_min_role": str(self.view_min_role),
            "not_queueable": self.not_queueable,
            "view": {str(r): role_rank(r) >= role_rank(self.view_min_role) for r in Role},
            "run": {str(r): self.can_run(r) for r in Role},
        }


def _spec(
    name: str,
    title: str,
    description: str,
    category: str,
    domains: tuple[str, ...],
    *,
    write: bool = False,
    external: bool = False,
    risk: RiskLevel = RiskLevel.LOW,
    classification: DataClassification = DataClassification.INTERNAL,
    permissions: Mapping[Role, ToolPermission] = matrix.READ_ALL,
    **kwargs,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        title=title,
        description=description,
        category=category,
        domains=frozenset(domains),
        write=write,
        external=external,
        risk=risk,
        classification=classification,
        permissions=permissions,
        **kwargs,
    )


_ALL = ()  # empty domain tuple == available to every enabled domain


_CAMPAIGN_NOT_QUEUEABLE = (
    "Campaign sends can't wait for approval under this workspace's current settings. "
    "Nothing was sent. An admin must send it directly, or change the setting that requires approval."
)


def _campaign(name: str, title: str, description: str, *, write: bool = False) -> ToolSpec:
    """An outbound-campaign tool: ADMIN-only and invisible to other roles."""
    return _spec(
        name,
        title,
        description,
        CATEGORY_CAMPAIGN,
        ("general",),
        write=write,
        external=write,
        risk=RiskLevel.MEDIUM if write else RiskLevel.LOW,
        permissions=matrix.ADMIN_ONLY,
        admin_config=write,
        view_min_role=Role.ADMIN,
        tags=frozenset({"write" if write else "read", "campaign"}),
        # The campaign selection and draft live only in the chat turn, so a queued call could never run.
        not_queueable=_CAMPAIGN_NOT_QUEUEABLE,
    )


def _admin(name: str, title: str, description: str, *, write: bool = False) -> ToolSpec:
    """An Administration-page capability, ADMIN-only and invisible to other roles."""
    return _spec(
        name,
        title,
        description,
        CATEGORY_ADMIN,
        _ALL,
        write=write,
        permissions=matrix.ADMIN_ONLY,
        admin_config=write,
        view_min_role=Role.ADMIN,
        tags=frozenset({"write" if write else "read", "admin"}),
    )

TOOL_SPECS: tuple[ToolSpec, ...] = (
    # ------------------------------------------------------------- memory
    _spec(
        "memory_search",
        "Search organizational memory",
        "Search approved facts, SOPs and decisions in the workspace's organizational memory.",
        CATEGORY_MEMORY,
        _ALL,
        tags=frozenset({"read", "memory"}),
    ),
    _spec(
        "memory_get_fact",
        "Get a fact",
        "Read the current active value of one organizational fact by its key.",
        CATEGORY_MEMORY,
        _ALL,
        tags=frozenset({"read", "memory"}),
    ),
    _spec(
        "memory_history",
        "Read memory history",
        "Show every version of one memory item, including superseded values and who changed them.",
        CATEGORY_MEMORY,
        _ALL,
        tags=frozenset({"read", "memory"}),
    ),
    _spec(
        "approvals_list",
        "List approvals and actions",
        "List actions by status — pending approval (default), drafts, approved, completed, "
        "failed, rejected, cancelled, or all — with what each one is, who requested and "
        "decided it and, for invoices, the amount. Optionally only the caller's own.",
        CATEGORY_INTERNAL,
        _ALL,
        tags=frozenset({"read", "approvals"}),
    ),
    _spec(
        "approvals_get",
        "Show one action",
        "Show one action with its content and full history: who requested, submitted, "
        "approved, rejected or ran it, when, and any comments or errors.",
        CATEGORY_INTERNAL,
        _ALL,
        tags=frozenset({"read", "approvals"}),
    ),
    _spec(
        "approvals_approve",
        "Approve an action",
        "Approve an action that is pending approval, and carry it out when this user may "
        "execute it. Refused unless the user holds the workspace's approver role, and "
        "refused for the user's own request when self-approval is off.",
        CATEGORY_INTERNAL,
        _ALL,
        write=True,
        permissions=matrix.DECIDE_APPROVAL,
        governs_queue=True,
        tags=frozenset({"write", "approvals"}),
    ),
    _spec(
        "approvals_reject",
        "Reject an action",
        "Reject an action that is pending approval, with an optional reason. It will not run.",
        CATEGORY_INTERNAL,
        _ALL,
        write=True,
        permissions=matrix.DECIDE_APPROVAL,
        governs_queue=True,
        tags=frozenset({"write", "approvals"}),
    ),
    _spec(
        "approvals_request_changes",
        "Send an action back for changes",
        "Return a pending action to its requester as a draft, with a comment saying what to change.",
        CATEGORY_INTERNAL,
        _ALL,
        write=True,
        permissions=matrix.DECIDE_APPROVAL,
        governs_queue=True,
        tags=frozenset({"write", "approvals"}),
    ),
    _spec(
        "approvals_submit",
        "Submit a draft for approval",
        "Put one of the user's saved drafts into the approval queue.",
        CATEGORY_INTERNAL,
        _ALL,
        write=True,
        permissions=matrix.WRITE_INTERNAL,
        governs_queue=True,
        tags=frozenset({"write", "approvals"}),
    ),
    # -------------------------------------------------------------- admin
    _admin(
        "admin_workspace_overview",
        "Workspace overview",
        "Summary of this workspace's configuration: mode, purchased and enabled packs, Order/DPA, "
        "approval and risk policy, per-tool modes, user counts, setup, launch, alerts and pricing status.",
    ),
    _admin("admin_list_users", "List users", "Every user in this workspace with roles, data access and status."),
    _admin("admin_get_setup", "Show first-client setup", "The setup profile: goals, systems, SOPs, notes, review points, exclusions, baselines, assessment findings and change orders."),
    _admin("admin_list_alerts", "List efficiency alerts", "Inefficiency alerts (open, held, resolved, dismissed) and before/after baselines."),
    _admin("admin_launch_checklist", "Show launch checklist", "Launch checklist items with status, and whether it is ready for sign-off."),
    _admin("admin_pricing", "Show pricing", "Resale price rules, consult scope weights and saved consult quotes."),
    _admin("admin_list_domains", "List domain packs", "Every domain pack with whether it is purchased and enabled."),
    _admin("admin_tool_permissions", "Show tool permissions", "Effective per-role permission and per-tool mode for tools, optionally filtered by name or category."),
    _admin("admin_apply_setup", "Apply first-client setup", "Save setup fields (goals, systems, SOPs, roles/access notes, review points, exclusions, autonomy mode, packs) and record them in memory. Fields left out keep their current values.", write=True),
    _admin("admin_handoff_assessment", "Prefill setup from an assessment", "Prefill setup from sale/assessment details (a JSON object).", write=True),
    _admin("admin_scan_alerts", "Scan for efficiency alerts", "Re-scan this workspace's data for recurring waste now.", write=True),
    _admin("admin_dismiss_alert", "Dismiss an alert", "Dismiss one efficiency alert by id.", write=True),
    _admin("admin_set_baselines", "Set baselines", "Add or update before/after baseline metrics, or remove some. Others are kept.", write=True),
    _admin("admin_mark_launch_item", "Update a launch checklist item", "Mark a launch checklist item done, pending or waived (waiving needs a reason).", write=True),
    _admin("admin_launch_sign_off", "Sign off launch", "Record go-live sign-off once the checklist is ready.", write=True),
    _admin("admin_set_resale_rules", "Set resale price rules", "Change the resale price, Cepoch cost or margin, quote status, notes or consult scope weights.", write=True),
    _admin("admin_consult_quote", "Calculate a consult quote", "Calculate a consult quote for chosen packs, autonomy, integrations and discount; optionally save it.", write=True),
    _admin("admin_create_user", "Add a user", "Create a user with roles and data access. A temporary password is generated and returned once.", write=True),
    _admin("admin_set_user_roles", "Change a user's roles", "Replace a user's roles (VIEWER, DRAFTER, APPROVER, OPERATOR, ADMIN).", write=True),
    _admin("admin_set_user_access", "Change a user's data access", "Grant or revoke restricted data areas for a user.", write=True),
    _admin("admin_set_user_status", "Suspend or reactivate a user", "Set a user ACTIVE or SUSPENDED.", write=True),
    _admin("admin_reset_password", "Reset a user's password", "Set a new temporary password for a user, returned once.", write=True),
    _admin("admin_update_settings", "Change workspace policies", "Change the workspace mode, Order/DPA categories, BAA coverage, approval, risk, memory or retention policy fields, or the workspace timezone.", write=True),
    _admin("admin_toggle_domain", "Enable or disable a pack", "Turn a purchased domain pack on or off.", write=True),
    _admin("admin_record_change_order", "Record a change order", "Add a domain pack to the purchased package against a change-order reference.", write=True),
    _admin("admin_set_tool_permission", "Change a tool permission", "Override one role's permission for one tool, within the registry ceiling, or reset it to default.", write=True),
    _admin("admin_set_tool_mode", "Set a tool's mode", "Give one tool its own execution mode (only stricter than the workspace), or clear it.", write=True),
    _spec(
        "approvals_cancel",
        "Cancel an action",
        "Cancel a draft, pending or approved action. Only its requester or an admin may.",
        CATEGORY_INTERNAL,
        _ALL,
        write=True,
        permissions=matrix.WRITE_INTERNAL,
        governs_queue=True,
        tags=frozenset({"write", "approvals"}),
    ),
    _spec(
        "memory_add_fact",
        "Record a fact",
        "Record a new organizational fact with its source and confidence.",
        CATEGORY_MEMORY,
        _ALL,
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.WRITE_INTERNAL,
        draft_safe=True,
        tags=frozenset({"write", "memory"}),
    ),
    _spec(
        "memory_add_sop",
        "Record an SOP",
        "Record a procedure, policy or playbook in organizational memory.",
        CATEGORY_MEMORY,
        _ALL,
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.WRITE_INTERNAL,
        draft_safe=True,
        tags=frozenset({"write", "memory"}),
    ),
    _spec(
        "memory_record_decision",
        "Record a decision",
        "Record a decision with its date, approver, context and status.",
        CATEGORY_MEMORY,
        _ALL,
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.WRITE_INTERNAL,
        draft_safe=True,
        tags=frozenset({"write", "memory"}),
    ),
    _spec(
        "memory_correct",
        "Correct organizational memory",
        "Supersede an existing memory value with a corrected one, preserving the previous "
        "version in history. Never overwrites.",
        CATEGORY_MEMORY,
        _ALL,
        write=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_INTERNAL,
        draft_safe=True,
        tags=frozenset({"write", "memory", "correction"}),
    ),
    _spec(
        "memory_escalate_conflict",
        "Escalate a policy conflict",
        "When recorded policies disagree, hand the conflict to a person to decide. Pass the "
        "topic, the conflicting item_ids and the question to resolve. Never pick a side.",
        CATEGORY_MEMORY,
        _ALL,
        write=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_INTERNAL,
        always_requires_approval=True,
        tags=frozenset({"write", "memory", "conflict"}),
    ),
    # ------------------------------------------------------------ support
    _spec(
        "support_create_ticket",
        "Raise a support ticket",
        "Open a ticket with Cepoch support for a problem with FreedomBot or the platform "
        "(something broken, access, setup, how-to). Routed to Cepoch, never to Jeanne. "
        "Not for buying, pricing or upgrades.",
        CATEGORY_INTERNAL,
        _ALL,
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.REQUEST_ALL,
        draft_safe=True,
        tags=frozenset({"write", "support"}),
    ),
    _spec(
        "support_my_tickets",
        "List my support tickets",
        "List the support tickets this user raised, with status and latest update.",
        CATEGORY_INTERNAL,
        _ALL,
        tags=frozenset({"read", "support"}),
    ),
    # --------------------------------------------------------------- CRM
    _spec(
        "crm_search_contacts",
        "Search CRM contacts",
        "Find contacts by name, email, company or tag.",
        CATEGORY_CRM,
        ("sales", "intake", "operations", "planning"),
        integration="crm",
        tags=frozenset({"read", "crm"}),
    ),
    _spec(
        "crm_get_contact",
        "Get a CRM contact",
        "Read one contact record with its properties and recent notes.",
        CATEGORY_CRM,
        ("sales", "intake", "operations", "planning"),
        integration="crm",
        tags=frozenset({"read", "crm"}),
    ),
    _spec(
        "crm_search_companies",
        "Search CRM companies",
        "Find company records by name or domain.",
        CATEGORY_CRM,
        ("sales", "intake", "operations"),
        integration="crm",
        tags=frozenset({"read", "crm"}),
    ),
    _spec(
        "crm_search_deals",
        "Search CRM deals",
        "Find deals by stage, owner, amount or close date.",
        CATEGORY_CRM,
        ("sales", "operations"),
        integration="crm",
        tags=frozenset({"read", "crm"}),
    ),
    _spec(
        "crm_pipeline_summary",
        "Summarize the pipeline",
        "Aggregate open deals by stage with totals and week-over-week movement.",
        CATEGORY_CRM,
        ("sales", "operations"),
        integration="crm",
        tags=frozenset({"read", "crm", "report"}),
    ),
    _spec(
        "crm_create_note",
        "Add a CRM note",
        "Attach an internal note to a contact or deal.",
        CATEGORY_CRM,
        ("sales", "intake", "operations"),
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.WRITE_INTERNAL,
        integration="crm",
        tags=frozenset({"write", "crm"}),
    ),
    _spec(
        "crm_create_task",
        "Create an internal task",
        "Create an internal follow-up task. Low risk: nothing leaves the workspace.",
        CATEGORY_CRM,
        ("sales", "intake", "operations", "planning"),
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.WRITE_INTERNAL,
        integration="crm",
        tags=frozenset({"write", "crm", "task"}),
    ),
    _spec(
        "crm_tag_lead",
        "Tag a CRM lead",
        "Add or remove tags on a lead, and set its lifecycle stage. Low-risk categorization.",
        CATEGORY_CRM,
        ("sales", "intake"),
        write=True,
        risk=RiskLevel.LOW,
        permissions=matrix.WRITE_INTERNAL,
        integration="crm",
        tags=frozenset({"write", "crm", "classification"}),
    ),
    _spec(
        "crm_update_contact_field",
        "Update a CRM contact field",
        "Change one field on one contact record.",
        CATEGORY_CRM,
        ("sales", "intake", "operations"),
        write=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_INTERNAL,
        integration="crm",
        draft_counterpart="crm_create_note",
        tags=frozenset({"write", "crm"}),
    ),
    _spec(
        "crm_create_contact",
        "Create a CRM contact",
        "Create a new contact record.",
        CATEGORY_CRM,
        ("sales", "intake"),
        write=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_INTERNAL,
        integration="crm",
        tags=frozenset({"write", "crm"}),
    ),
    _spec(
        "crm_update_deal_stage",
        "Move a deal stage",
        "Advance or regress a deal's pipeline stage.",
        CATEGORY_CRM,
        ("sales",),
        write=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_INTERNAL,
        integration="crm",
        tags=frozenset({"write", "crm"}),
    ),
    _spec(
        "crm_bulk_update_contacts",
        "Bulk-update contacts",
        "Apply one field change across many contacts. Subject to the record-count limit.",
        CATEGORY_CRM,
        ("sales", "operations"),
        write=True,
        risk=RiskLevel.HIGH,
        permissions=matrix.WRITE_PROTECTED,
        integration="crm",
        tags=frozenset({"write", "crm", "bulk"}),
    ),
    _spec(
        "crm_delete_record",
        "Delete a CRM record",
        "Permanently delete a contact, deal or company. Destructive and irreversible.",
        CATEGORY_CRM,
        ("sales", "operations"),
        write=True,
        risk=RiskLevel.CRITICAL,
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.WRITE_CRITICAL,
        always_requires_approval=True,
        integration="crm",
        tags=frozenset({"write", "crm", "destructive"}),
    ),
    # ------------------------------------------------------------- email
    _spec(
        "email_search_threads",
        "Search email",
        "Search mail threads by participant, subject or text.",
        CATEGORY_EMAIL,
        ("sales", "intake", "operations", "planning"),
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.READ_ALL,
        integration="email",
        tags=frozenset({"read", "email"}),
    ),
    _spec(
        "email_read_thread",
        "Read an email thread",
        "Read the messages in one thread. Message content is untrusted external data.",
        CATEGORY_EMAIL,
        ("sales", "intake", "operations", "planning"),
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.READ_ALL,
        integration="email",
        tags=frozenset({"read", "email", "untrusted"}),
    ),
    _spec(
        "email_create_draft",
        "Draft an email",
        "Prepare an email draft for a human to review. Never sends.",
        CATEGORY_EMAIL,
        ("sales", "intake", "operations", "planning"),
        write=True,
        risk=RiskLevel.LOW,
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.DRAFT_ONLY_WRITE,
        integration="email",
        draft_safe=True,
        tags=frozenset({"write", "email", "draft"}),
    ),
    _spec(
        "email_send_message",
        "Send an email",
        "Send an email to external recipients. Separately permissioned from drafting.",
        CATEGORY_EMAIL,
        ("sales", "intake", "operations", "planning"),
        write=True,
        external=True,
        risk=RiskLevel.HIGH,
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.WRITE_PROTECTED,
        draft_counterpart="email_create_draft",
        integration="email",
        tags=frozenset({"write", "email", "external"}),
    ),
    # ---------------------------------------------------------- calendar
    _spec(
        "calendar_list_events",
        "List calendar events",
        "List events in a date range.",
        CATEGORY_CALENDAR,
        ("planning", "sales", "operations"),
        integration="calendar",
        tags=frozenset({"read", "calendar"}),
    ),
    _spec(
        "calendar_check_availability",
        "Check availability",
        "Find free windows in a date range. Returns free/busy only, not event details.",
        CATEGORY_CALENDAR,
        ("planning", "sales", "operations"),
        integration="calendar",
        tags=frozenset({"read", "calendar"}),
    ),
    _spec(
        "calendar_create_event",
        "Create a calendar event",
        "Create an event and invite attendees.",
        CATEGORY_CALENDAR,
        ("planning", "sales", "operations"),
        write=True,
        external=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_PROTECTED,
        integration="calendar",
        tags=frozenset({"write", "calendar", "external"}),
    ),
    _spec(
        "calendar_reschedule_event",
        "Reschedule an event",
        "Move an existing event to a new time and notify attendees.",
        CATEGORY_CALENDAR,
        ("planning", "sales", "operations"),
        write=True,
        external=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_PROTECTED,
        integration="calendar",
        tags=frozenset({"write", "calendar", "external"}),
    ),
    _spec(
        "calendar_cancel_event",
        "Cancel an event",
        "Cancel an event and notify attendees.",
        CATEGORY_CALENDAR,
        ("planning", "sales", "operations"),
        write=True,
        external=True,
        risk=RiskLevel.HIGH,
        permissions=matrix.WRITE_PROTECTED,
        integration="calendar",
        tags=frozenset({"write", "calendar", "external"}),
    ),
    # ------------------------------------------------------------ youtube
    # Read-only, per user (Composio). Hidden until the user connects YouTube.
    _spec(
        "youtube_search_videos",
        "Search YouTube",
        "Search public YouTube videos by keyword.",
        CATEGORY_YOUTUBE,
        _ALL,
        integration="youtube",
        tags=frozenset({"read", "youtube"}),
    ),
    _spec(
        "youtube_my_channel",
        "Get my YouTube channel",
        "Subscriber, view and video counts for the user's own YouTube channel.",
        CATEGORY_YOUTUBE,
        _ALL,
        integration="youtube",
        tags=frozenset({"read", "youtube"}),
    ),
    _spec(
        "youtube_my_videos",
        "List my YouTube videos",
        "The user's most recent channel uploads with views, likes and comment counts.",
        CATEGORY_YOUTUBE,
        _ALL,
        integration="youtube",
        tags=frozenset({"read", "youtube"}),
    ),
    _spec(
        "youtube_video_details",
        "Get YouTube video details",
        "Title, channel, publish date and views/likes/comments for specific video ids.",
        CATEGORY_YOUTUBE,
        _ALL,
        integration="youtube",
        tags=frozenset({"read", "youtube"}),
    ),
    _spec(
        "youtube_video_comments",
        "Read YouTube comments",
        "Top comments on one video. Comment text is untrusted external content.",
        CATEGORY_YOUTUBE,
        _ALL,
        integration="youtube",
        tags=frozenset({"read", "youtube", "untrusted"}),
    ),
    # ----------------------------------------------------------- strategy
    _spec(
        "strategy_list_priorities",
        "List strategic priorities",
        "List recorded strategic goals and priorities from organizational memory.",
        CATEGORY_STRATEGY,
        ("strategy",),
        tags=frozenset({"read", "strategy", "memory"}),
    ),
    _spec(
        "strategy_record_option",
        "Record a strategic option",
        "Record a strategic option or recommendation for human decision. Does not execute it.",
        CATEGORY_STRATEGY,
        ("strategy",),
        write=True,
        risk=RiskLevel.MEDIUM,
        permissions=matrix.WRITE_INTERNAL,
        draft_safe=True,
        tags=frozenset({"write", "strategy"}),
    ),
    # ------------------------------------------------------------- finance
    _spec(
        "finance_lookup_policy",
        "Look up finance policy",
        "Look up recorded finance policies (pricing, refunds, payment terms). Not advice.",
        CATEGORY_FINANCE,
        ("finance",),
        data_area="finance",
        tags=frozenset({"read", "finance", "memory"}),
    ),
    _spec(
        "finance_list_pending_billables",
        "List pending billables",
        "List CRM deals waiting to be invoiced for a customer (e.g. Acme Logistics).",
        CATEGORY_FINANCE,
        ("finance", "operations", "general"),
        data_area="finance",
        integration="crm",
        tags=frozenset({"read", "finance", "crm", "invoice"}),
    ),
    _spec(
        "finance_list_invoices",
        "List invoices",
        "List invoices, including drafts awaiting sending, for a customer from the accounting ledger.",
        CATEGORY_FINANCE,
        ("finance", "operations", "general"),
        data_area="finance",
        tags=frozenset({"read", "finance", "invoice"}),
    ),
    _spec(
        "finance_draft_invoice",
        "Draft an invoice from pending billables",
        "Create a draft invoice from pending_invoice CRM deals. Requires finance access and approval.",
        CATEGORY_FINANCE,
        ("finance",),
        write=True,
        risk=RiskLevel.HIGH,
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.WRITE_PROTECTED,
        always_requires_approval=True,
        data_area="finance",
        view_min_role=Role.DRAFTER,
        integration="crm",
        tags=frozenset({"write", "finance", "invoice"}),
    ),
    _spec(
        "finance_propose_adjustment",
        "Propose a finance adjustment",
        "Propose a payment or balance adjustment. Always requires licensed human approval.",
        CATEGORY_FINANCE,
        ("finance",),
        write=True,
        external=True,
        risk=RiskLevel.CRITICAL,
        classification=DataClassification.RESTRICTED,
        permissions=matrix.WRITE_CRITICAL,
        always_requires_approval=True,
        licensed_judgment=True,
        financial=True,
        data_area="finance",
        view_min_role=Role.DRAFTER,
        tags=frozenset({"write", "finance", "licensed"}),
    ),
    # ------------------------------------------------------------------ hr
    _spec(
        "hr_match_candidate",
        "Match a resume to a job opening",
        "Compare a candidate's resume against a job opening. Candidates, resumes and job "
        "openings live in HR memory — NOT in the CRM — so use this for any 'does <candidate> "
        "fit <role>' question. Assistive only — not a hiring decision.",
        CATEGORY_HR,
        ("intake", "operations", "general"),
        data_area="hr",
        tags=frozenset({"read", "hr", "hiring"}),
    ),
    # --------------------------------------------------------------- brand
    _spec(
        "brand_get_voice",
        "Get brand voice guidelines",
        "Retrieve brand voice and messaging guidelines from organizational memory.",
        CATEGORY_BRAND,
        ("brand",),
        tags=frozenset({"read", "brand", "memory"}),
    ),
    # --------------------------------------------------------------- legal
    _spec(
        "legal_find_clause",
        "Find a recorded clause summary",
        "Find recorded contract/clause summaries. Never a legal conclusion.",
        CATEGORY_LEGAL,
        ("legal",),
        data_area="legal",
        tags=frozenset({"read", "legal", "memory"}),
    ),
    _spec(
        "legal_flag_for_counsel",
        "Flag a matter for counsel",
        "Escalate a legal question to human counsel. Always requires approval.",
        CATEGORY_LEGAL,
        ("legal",),
        write=True,
        risk=RiskLevel.HIGH,
        classification=DataClassification.CONFIDENTIAL,
        permissions=matrix.WRITE_PROTECTED,
        always_requires_approval=True,
        licensed_judgment=True,
        data_area="legal",
        tags=frozenset({"write", "legal", "licensed"}),
    ),
    # ------------------------------------------------------------------ n8n
    _spec(
        "n8n_trigger_webhook",
        "Trigger an n8n webhook",
        "Call the client's n8n webhook with a structured event after approval.",
        CATEGORY_N8N,
        ("operations", "sales", "intake", "planning"),
        write=True,
        external=True,
        risk=RiskLevel.HIGH,
        permissions=matrix.WRITE_PROTECTED,
        always_requires_approval=True,
        integration="n8n",
        tags=frozenset({"write", "n8n", "external"}),
    ),
    # ------------------------------------------------------------ campaign
    # The admin-only outbound campaign flow (app/assistant/assistant.py, CampaignSession).
    # Declared under "general" so the domain-selected packs never pick them up; the
    # assistant adds them for admins only. All but send_approved change nothing except
    # the conversation's saved selection, so they are reads to the policy check and work
    # in every mode, as before. send_approved is the admin's own authority, like the
    # Administration page: it skips the mode queue (Advise still forbids it), and the
    # human gate is the user's own approval of the email card, checked in CampaignSession.
    *(
        _campaign(name, title, description)
        for name, title, description in (
            ("update_selection", "Commit campaign selection",
             "Save the recipients, campaign and daily send window the user chose in this conversation."),
            ("show_contacts", "Show campaign contacts", "Show the saved prospects table so the user can pick."),
            ("show_campaigns", "Show campaigns", "Show the saved campaigns table so the user can pick one."),
            ("propose_email", "Propose campaign email",
             "Build the campaign email draft from the committed selection and show it for approval."),
            ("approve_move", "Approve moving prospects",
             "Record the user's yes to moving people out of another campaign into this one."),
        )
    ),
    _campaign(
        "send_approved",
        "Send approved campaign email",
        "Create the campaign and send the email the user approved to the committed recipients.",
        write=True,
    ),
)

TOOLS_BY_NAME: Mapping[str, ToolSpec] = MappingProxyType({s.name: s for s in TOOL_SPECS})


def get_spec(name: str) -> Optional[ToolSpec]:
    """Look up a tool spec, or ``None`` if the tool is not registered.

    An unregistered tool is treated as denied everywhere: the policy engine has
    no metadata to reason about, so it must fail closed.
    """
    return TOOLS_BY_NAME.get(name)


def specs_for_category(category: str) -> tuple[ToolSpec, ...]:
    return tuple(s for s in TOOL_SPECS if s.category == category)


def specs_for_domain(domain: str) -> tuple[ToolSpec, ...]:
    """Tools a domain pack may expose (empty ``domains`` means every domain)."""
    folded = (domain or "").strip().casefold()
    return tuple(s for s in TOOL_SPECS if not s.domains or folded in s.domains)


#: Patterns that would constitute a generic, unrestricted action tool (§19, §28).
#: Enforced by a test, so the rule cannot rot.
_GENERIC_TOOL_PATTERNS = (
    "execute_any",
    "run_any",
    "arbitrary",
    "execute_api",
    "api_request",
    "http_request",
    "raw_sql",
    "execute_sql",
    "eval",
    "exec_",
    "shell",
    "generic_",
)


def assert_no_generic_tools() -> None:
    """Raise if any registered tool looks like a general-purpose executor."""
    offenders = [
        s.name
        for s in TOOL_SPECS
        if any(pattern in s.name.casefold() for pattern in _GENERIC_TOOL_PATTERNS)
    ]
    if offenders:
        raise AssertionError(
            f"Generic action tools are forbidden (§19). Offending registrations: {offenders}"
        )
