"""Audit routes (§26 /api/audit)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import bound, current_context, require_role
from bizos.audit import events as audit
from bizos.tenancy.context import TenantContext
from bizos.types import AuditEventType, Role

router = APIRouter(prefix="/api/audit", tags=["audit"])

#: Reading the audit log is itself sensitive: it contains who did what with which
#: records. Viewers and drafters do not get it.
_reader = require_role(Role.APPROVER, Role.OPERATOR, Role.ADMIN)


@router.get("")
def search(
    event_type: Optional[str] = None,
    user_id: Optional[str] = None,
    tool: Optional[str] = None,
    integration: Optional[str] = None,
    workflow_id: Optional[str] = None,
    action_id: Optional[str] = None,
    status: Optional[str] = None,
    decision: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    ctx: TenantContext = Depends(_reader),
) -> dict[str, Any]:
    """Filter the workspace's append-only audit trail."""
    with bound(ctx):
        rows = audit.search(
            ctx=ctx,
            event_types=[event_type] if event_type else None,
            user_id=user_id,
            tool=tool,
            integration=integration,
            workflow_id=workflow_id,
            action_id=action_id,
            status=status,
            decision=decision,
            date_from=date_from,
            date_to=date_to,
            query=q,
            limit=limit,
            offset=offset,
        )
        return {"items": rows, "count": len(rows), "total": audit.count(ctx)}


@router.get("/event-types")
def event_types() -> dict[str, list[str]]:
    """The vocabulary, for the UI's filter dropdown."""
    return {"event_types": [str(e) for e in AuditEventType]}


@router.get("/retention")
def retention(ctx: TenantContext = Depends(_reader)) -> dict[str, Any]:
    """The audit retention rule in force for this workspace."""
    from bizos.control import store as control

    days = control.get_client(ctx.client_id).settings.retention_policy.audit_days
    return {
        "audit_days": days,
        "effective_days": max(days, audit.MIN_AUDIT_RETENTION_DAYS) if days > 0 else 0,
        "minimum_days": audit.MIN_AUDIT_RETENTION_DAYS,
    }


@router.post("/purge")
def purge(ctx: TenantContext = Depends(require_role(Role.ADMIN))) -> dict[str, Any]:
    """Apply the retention rule now: delete only rows older than the window."""
    from bizos.control import store as control

    days = control.get_client(ctx.client_id).settings.retention_policy.audit_days
    with bound(ctx):
        return audit.purge_expired(days, ctx=ctx)
