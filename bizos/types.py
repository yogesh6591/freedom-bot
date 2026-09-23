"""
Shared Platform Types
=====================

Every enum the governance layer keys off lives here, in one module, so that the
policy engine, the tool registry, the storage layer and the API all agree on the
vocabulary. Nothing in this module imports from the rest of the platform, which
keeps it safe to import from anywhere.

All enums are ``str`` subclasses so they serialize to their value in JSON and
compare equal to the plain strings stored in Postgres.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class StrEnum(str, Enum):
    """A string enum whose ``str()`` is the bare value (not ``Class.MEMBER``)."""

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def parse(cls, value: object, default: Optional["StrEnum"] = None):
        """Coerce ``value`` to a member, case-insensitively.

        Returns ``default`` for unknown input rather than raising: this is used on
        data crossing a trust boundary (request bodies, DB rows written by an older
        version), where an unrecognized value must fail closed to the caller's
        default, not take the process down.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            folded = value.strip().casefold()
            for member in cls:
                if member.value.casefold() == folded or member.name.casefold() == folded:
                    return member
        return default


class Role(StrEnum):
    """Platform roles, in ascending order of capability.

    The ordering is meaningful: :func:`role_rank` turns it into an integer so a
    check can ask "is this caller at least a DRAFTER?" rather than enumerating
    every role that qualifies. Note that rank is *not* the whole permission story
    — APPROVER can approve but does not automatically get OPERATOR's execute
    right, so tool permissions are declared per role, not by threshold alone.
    """

    VIEWER = "VIEWER"
    DRAFTER = "DRAFTER"
    APPROVER = "APPROVER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


_ROLE_ORDER = {
    Role.VIEWER: 0,
    Role.DRAFTER: 1,
    Role.APPROVER: 2,
    Role.OPERATOR: 3,
    Role.ADMIN: 4,
}


def role_rank(role: Role) -> int:
    """Ascending capability rank for ``role``."""
    return _ROLE_ORDER[role]


class ToolPermission(StrEnum):
    """What a given role may do with a given tool.

    ``DENIED``       — the tool is never placed on this role's surface.
    ``ALLOWED``      — may invoke directly (subject to mode and policy).
    ``DRAFT_ONLY``   — may only produce a draft; the write never reaches the connector.
    ``APPROVE_ONLY`` — may approve someone else's proposal, but not originate one.
    ``EXECUTE_AFTER_APPROVAL`` — may execute, but only an already-APPROVED action.
    """

    DENIED = "DENIED"
    ALLOWED = "ALLOWED"
    DRAFT_ONLY = "DRAFT_ONLY"
    APPROVE_ONLY = "APPROVE_ONLY"
    EXECUTE_AFTER_APPROVAL = "EXECUTE_AFTER_APPROVAL"


class ExecutionMode(StrEnum):
    """The four execution modes from the brief (§5).

    Ordered from most to least restrictive. A per-request override may only move
    *down* this list (toward more restriction), never up — see
    :func:`bizos.policy.modes.resolve_mode`.
    """

    ADVISE = "ADVISE"
    DRAFT = "DRAFT"
    WAIT_FOR_APPROVAL = "WAIT_FOR_APPROVAL"
    AUTO_WITHIN_SCOPE = "AUTO_WITHIN_SCOPE"


_MODE_RESTRICTION = {
    ExecutionMode.ADVISE: 0,
    ExecutionMode.DRAFT: 1,
    ExecutionMode.WAIT_FOR_APPROVAL: 2,
    ExecutionMode.AUTO_WITHIN_SCOPE: 3,
}


def mode_permissiveness(mode: ExecutionMode) -> int:
    """How permissive ``mode`` is; larger means more is allowed."""
    return _MODE_RESTRICTION[mode]


class RiskLevel(StrEnum):
    """Action risk classification (§22)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def risk_rank(risk: RiskLevel) -> int:
    """Ascending severity rank for ``risk``."""
    return _RISK_ORDER[risk]


class DataClassification(StrEnum):
    """Sensitivity of the data a tool touches (§15)."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


_CLASSIFICATION_ORDER = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.RESTRICTED: 3,
}


def classification_rank(classification: DataClassification) -> int:
    """Ascending sensitivity rank."""
    return _CLASSIFICATION_ORDER[classification]


class PolicyEffect(StrEnum):
    """The four outcomes the policy engine can return (§6)."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    DRAFT_ONLY = "DRAFT_ONLY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ActionStatus(StrEnum):
    """Business-action lifecycle (§8)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Legal state transitions. Enforced in :mod:`bizos.actions.store`; an attempt to
#: move outside this graph raises rather than silently writing an impossible row.
ACTION_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.DRAFT: frozenset({ActionStatus.PENDING_APPROVAL, ActionStatus.CANCELLED}),
    ActionStatus.PENDING_APPROVAL: frozenset(
        {ActionStatus.APPROVED, ActionStatus.REJECTED, ActionStatus.DRAFT, ActionStatus.CANCELLED}
    ),
    ActionStatus.APPROVED: frozenset({ActionStatus.EXECUTING, ActionStatus.CANCELLED}),
    ActionStatus.EXECUTING: frozenset({ActionStatus.COMPLETED, ActionStatus.FAILED}),
    ActionStatus.REJECTED: frozenset(),
    ActionStatus.COMPLETED: frozenset(),
    ActionStatus.FAILED: frozenset({ActionStatus.EXECUTING}),  # retry after a transient failure
    ActionStatus.CANCELLED: frozenset(),
}


class MemoryCategory(StrEnum):
    """Organizational memory categories (§2)."""

    FACT = "FACT"
    SOP = "SOP"
    DECISION = "DECISION"
    CORRECTION = "CORRECTION"


class MemoryStatus(StrEnum):
    """Lifecycle of a memory *version*."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class ApprovalStatus(StrEnum):
    """Whether a memory version has been vetted by a human (§3)."""

    APPROVED = "APPROVED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class SourceType(StrEnum):
    """Provenance of a memory item (§3).

    ``AI_INFERENCE`` is deliberately last and is treated as least trusted: it
    defaults to ``ApprovalStatus.PENDING`` and is ranked below explicit sources
    during retrieval.
    """

    MANUAL = "MANUAL"
    DOCUMENT = "DOCUMENT"
    SLACK = "SLACK"
    GMAIL = "GMAIL"
    CRM = "CRM"
    CALENDAR = "CALENDAR"
    ACCOUNTING = "ACCOUNTING"
    WORKFLOW = "WORKFLOW"
    AI_INFERENCE = "AI_INFERENCE"


#: Trust weight per source, used to order retrieval when two active versions
#: could answer the same question. Explicit human input outranks inference.
SOURCE_TRUST: dict[SourceType, float] = {
    SourceType.MANUAL: 1.0,
    SourceType.DOCUMENT: 0.9,
    SourceType.CRM: 0.8,
    SourceType.ACCOUNTING: 0.8,
    SourceType.CALENDAR: 0.7,
    SourceType.GMAIL: 0.6,
    SourceType.SLACK: 0.6,
    SourceType.WORKFLOW: 0.6,
    SourceType.AI_INFERENCE: 0.3,
}


class ReviewStatus(StrEnum):
    """Human review queue item status (§16)."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class DeploymentType(StrEnum):
    """How a client's workspace is physically isolated (§1).

    ``DEDICATED_DB``     — its own Postgres database. Phase 1 default.
    ``DEDICATED_SCHEMA`` — its own Postgres schema inside a shared database.

    Both put a client's tables in their own namespace. Neither uses a shared
    table discriminated by a ``tenant_id`` column for customer content.
    """

    DEDICATED_DB = "DEDICATED_DB"
    DEDICATED_SCHEMA = "DEDICATED_SCHEMA"


class AuditEventType(StrEnum):
    """Structured audit event names (§9)."""

    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    KNOWLEDGE_LOOKUP = "KNOWLEDGE_LOOKUP"
    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_CORRECTED = "MEMORY_CORRECTED"
    MEMORY_READ = "MEMORY_READ"
    TOOL_CALL = "TOOL_CALL"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    POLICY_DECISION = "POLICY_DECISION"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_CHANGES_REQUESTED = "APPROVAL_CHANGES_REQUESTED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_FAILED = "ACTION_FAILED"
    CONNECTOR_RESPONSE = "CONNECTOR_RESPONSE"
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    HUMAN_REVIEW_CREATED = "HUMAN_REVIEW_CREATED"
    HUMAN_REVIEW_RESOLVED = "HUMAN_REVIEW_RESOLVED"
    GUARDRAIL_TRIGGERED = "GUARDRAIL_TRIGGERED"


__all__ = [
    "StrEnum",
    "Role",
    "role_rank",
    "ToolPermission",
    "ExecutionMode",
    "mode_permissiveness",
    "RiskLevel",
    "risk_rank",
    "DataClassification",
    "classification_rank",
    "PolicyEffect",
    "ActionStatus",
    "ACTION_TRANSITIONS",
    "MemoryCategory",
    "MemoryStatus",
    "ApprovalStatus",
    "SourceType",
    "SOURCE_TRUST",
    "ReviewStatus",
    "DeploymentType",
    "AuditEventType",
]
