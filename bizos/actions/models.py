"""Typed views over the action and approval tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from bizos.types import ActionStatus, DataClassification, ExecutionMode, RiskLevel


@dataclass
class Action:
    """A proposed business action, with a lifecycle that outlives the run.

    This is deliberately not agno's ``Approval`` record. Agno's models "this run
    is paused until someone clicks"; this models "this business action exists,
    was proposed for a reason, may be edited, approved, executed, retried or
    cancelled, and every transition is recorded" — which is what §8 asks for.
    """

    id: str
    title: str
    description: str
    explanation: str
    status: ActionStatus
    tool: str
    payload: dict[str, Any]
    risk_level: RiskLevel
    classification: DataClassification
    execution_mode: ExecutionMode
    policy_reason: str
    requested_by: str
    requested_by_role: Optional[str] = None
    integration: Optional[str] = None
    domain: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    record_count: int = 1
    financial_amount: Optional[Decimal] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    expires_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    executed_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    #: Populated by list/detail reads.
    approval: Optional["ApprovalRequest"] = None
    events: list["ApprovalEvent"] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ActionStatus.COMPLETED,
            ActionStatus.REJECTED,
            ActionStatus.CANCELLED,
        }

    def to_dict(self, *, include_events: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "explanation": self.explanation,
            "status": str(self.status),
            "tool": self.tool,
            "integration": self.integration,
            "domain": self.domain,
            "payload": self.payload,
            "risk_level": str(self.risk_level),
            "classification": str(self.classification),
            "execution_mode": str(self.execution_mode),
            "policy_reason": self.policy_reason,
            "requested_by": self.requested_by,
            "requested_by_role": self.requested_by_role,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_run_id": self.workflow_run_id,
            "record_count": self.record_count,
            "financial_amount": float(self.financial_amount) if self.financial_amount is not None else None,
            "result": self.result,
            "error": self.error,
            "expires_at": _iso(self.expires_at),
            "executed_at": _iso(self.executed_at),
            "executed_by": self.executed_by,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "approval": self.approval.to_dict() if self.approval else None,
        }
        if include_events:
            data["events"] = [e.to_dict() for e in self.events]
        return data


@dataclass
class ApprovalRequest:
    """The open ask sitting in the approval queue for one action."""

    id: str
    action_id: str
    status: ActionStatus
    reason: str
    required_role: str
    requested_by: str
    assigned_to: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    comment: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action_id": self.action_id,
            "status": str(self.status),
            "reason": self.reason,
            "required_role": self.required_role,
            "requested_by": self.requested_by,
            "assigned_to": self.assigned_to,
            "decided_by": self.decided_by,
            "decided_at": _iso(self.decided_at),
            "comment": self.comment,
            "expires_at": _iso(self.expires_at),
            "created_at": _iso(self.created_at),
        }


@dataclass
class ApprovalEvent:
    """One immutable state transition."""

    id: int
    action_id: str
    event: str
    from_status: Optional[str]
    to_status: Optional[str]
    actor_id: str
    actor_role: Optional[str] = None
    comment: Optional[str] = None
    payload_before: Optional[dict[str, Any]] = None
    payload_after: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action_id": self.action_id,
            "event": self.event,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role,
            "comment": self.comment,
            "payload_before": self.payload_before,
            "payload_after": self.payload_after,
            "created_at": _iso(self.created_at),
        }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None
