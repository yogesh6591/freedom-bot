"""
Human Review Queue
==================

§16: when the agent is uncertain about something that matters, it should not
guess. It creates a review item — question, context, the options it considered,
its confidence and its recommendation — and stops.

After a human resolves the item, the workflow that raised it can continue from
the recorded answer (:func:`resolution_for`), which is what makes "ask instead of
guess" a usable pattern rather than a dead end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import text

from bizos.tenancy.context import TenantContext, require_context
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.types import AuditEventType, ReviewStatus
from bizos.util.ids import new_id


class ReviewNotFound(LookupError):
    pass


@dataclass
class ReviewItem:
    id: str
    question: str
    context: str
    choices: list[Any]
    status: ReviewStatus
    recommended_option: Optional[str] = None
    confidence: Optional[float] = None
    agent_id: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    session_id: Optional[str] = None
    action_id: Optional[str] = None
    created_by: str = ""
    resolved_by: Optional[str] = None
    resolution: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "context": self.context,
            "choices": self.choices,
            "status": str(self.status),
            "recommended_option": self.recommended_option,
            "confidence": self.confidence,
            "agent_id": self.agent_id,
            "workflow_id": self.workflow_id,
            "workflow_run_id": self.workflow_run_id,
            "session_id": self.session_id,
            "action_id": self.action_id,
            "created_by": self.created_by,
            "resolved_by": self.resolved_by,
            "resolution": self.resolution,
            "resolution_note": self.resolution_note,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _from_row(row: Any) -> ReviewItem:
    return ReviewItem(
        id=row.id,
        question=row.question,
        context=row.context,
        choices=row.choices or [],
        status=ReviewStatus.parse(row.status, ReviewStatus.OPEN),  # type: ignore[arg-type]
        recommended_option=row.recommended_option,
        confidence=row.confidence,
        agent_id=row.agent_id,
        workflow_id=row.workflow_id,
        workflow_run_id=row.workflow_run_id,
        session_id=row.session_id,
        action_id=row.action_id,
        created_by=row.created_by,
        resolved_by=row.resolved_by,
        resolution=row.resolution,
        resolution_note=row.resolution_note,
        resolved_at=row.resolved_at,
        created_at=row.created_at,
    )


def create(
    *,
    question: str,
    context: str = "",
    choices: Optional[Sequence[Any]] = None,
    recommended_option: Optional[str] = None,
    confidence: Optional[float] = None,
    agent_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    session_id: Optional[str] = None,
    action_id: Optional[str] = None,
    ctx: Optional[TenantContext] = None,
) -> ReviewItem:
    tenant = ctx or require_context()
    item_id = new_id("rev")
    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO human_review_items (id, question, context, choices, "
                "recommended_option, confidence, agent_id, workflow_id, workflow_run_id, "
                "session_id, action_id, created_by) VALUES "
                "(:i, :q, :c, CAST(:ch AS JSONB), :r, :cf, :a, :w, :wr, :s, :ac, :u)"
            ),
            {
                "i": item_id,
                "q": question,
                "c": context,
                "ch": json.dumps(list(choices or []), default=str),
                "r": recommended_option,
                "cf": confidence,
                "a": agent_id,
                "w": workflow_id,
                "wr": workflow_run_id,
                "s": session_id,
                "ac": action_id,
                "u": tenant.user_id,
            },
        )
    _audit(AuditEventType.HUMAN_REVIEW_CREATED, tenant, review_id=item_id, question=question)
    return get(item_id, ctx=tenant)


def get(item_id: str, *, ctx: Optional[TenantContext] = None) -> ReviewItem:
    with readonly_connection(ctx) as conn:
        row = conn.execute(
            text("SELECT * FROM human_review_items WHERE id = :i"), {"i": item_id}
        ).first()
    if row is None:
        raise ReviewNotFound(item_id)
    return _from_row(row)


def list_items(
    *,
    status: Optional[ReviewStatus] = None,
    workflow_run_id: Optional[str] = None,
    limit: int = 100,
    ctx: Optional[TenantContext] = None,
) -> list[ReviewItem]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
    if status is not None:
        clauses.append("status = :status")
        params["status"] = str(status)
    if workflow_run_id:
        clauses.append("workflow_run_id = :wr")
        params["wr"] = workflow_run_id
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with readonly_connection(ctx) as conn:
        rows = conn.execute(
            text(f"SELECT * FROM human_review_items {where} ORDER BY created_at DESC LIMIT :limit"),
            params,
        ).fetchall()
    return [_from_row(r) for r in rows]


def resolve(
    item_id: str, *, resolution: str, note: str = "", ctx: Optional[TenantContext] = None
) -> ReviewItem:
    """Record a human's answer. The workflow may then continue from it."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        result = conn.execute(
            text(
                "UPDATE human_review_items SET status = :s, resolution = :r, resolution_note = :n, "
                "resolved_by = :u, resolved_at = NOW(), updated_at = NOW() "
                "WHERE id = :i AND status = :open"
            ),
            {
                "s": str(ReviewStatus.RESOLVED),
                "r": resolution,
                "n": note,
                "u": tenant.user_id,
                "i": item_id,
                "open": str(ReviewStatus.OPEN),
            },
        )
        if result.rowcount == 0:
            raise ReviewNotFound(f"{item_id} is not an open review item")
    _audit(
        AuditEventType.HUMAN_REVIEW_RESOLVED,
        tenant,
        review_id=item_id,
        resolution=resolution,
    )
    return get(item_id, ctx=tenant)


def dismiss(item_id: str, *, note: str = "", ctx: Optional[TenantContext] = None) -> ReviewItem:
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        result = conn.execute(
            text(
                "UPDATE human_review_items SET status = :s, resolution_note = :n, resolved_by = :u, "
                "resolved_at = NOW(), updated_at = NOW() WHERE id = :i AND status = :open"
            ),
            {
                "s": str(ReviewStatus.DISMISSED),
                "n": note,
                "u": tenant.user_id,
                "i": item_id,
                "open": str(ReviewStatus.OPEN),
            },
        )
        if result.rowcount == 0:
            raise ReviewNotFound(f"{item_id} is not an open review item")
    _audit(AuditEventType.HUMAN_REVIEW_RESOLVED, tenant, review_id=item_id, resolution="dismissed")
    return get(item_id, ctx=tenant)


def resolution_for(item_id: str, *, ctx: Optional[TenantContext] = None) -> Optional[str]:
    """The human's answer, if the item has been resolved. ``None`` while open.

    A workflow step calls this to decide whether it can continue.
    """
    item = get(item_id, ctx=ctx)
    return item.resolution if item.status == ReviewStatus.RESOLVED else None


def open_count(ctx: Optional[TenantContext] = None) -> int:
    with readonly_connection(ctx) as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM human_review_items WHERE status = 'OPEN'")
            ).scalar()
            or 0
        )


def _audit(event_type: AuditEventType, tenant: TenantContext, **payload: Any) -> None:
    from bizos.audit.events import log

    log(event_type, ctx=tenant, request={k: str(v) for k, v in payload.items()})
