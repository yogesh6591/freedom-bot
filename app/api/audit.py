"""Audit routes (§26 /api/audit)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import bound, current_context, require_role
from freedombot.audit import events as audit
from freedombot.tenancy.context import TenantContext
from freedombot.types import AuditEventType, Role

router = APIRouter(prefix="/api/audit", tags=["audit"])

#: Reading the audit log is itself sensitive: it contains who did what with which
#: records. Viewers and drafters do not get it.
_reader = require_role(Role.APPROVER, Role.OPERATOR, Role.ADMIN)

#: Short human labels for the Audit UI "What" column.
_EVENT_LABELS: dict[str, str] = {
    "LOGIN": "Signed in",
    "LOGIN_FAILED": "Failed sign-in",
    "LOGOUT": "Signed out",
    "KNOWLEDGE_LOOKUP": "Looked up knowledge",
    "MEMORY_CREATED": "Created memory",
    "MEMORY_CORRECTED": "Corrected memory",
    "MEMORY_ARCHIVED": "Archived memory",
    "MEMORY_READ": "Read memory",
    "TOOL_CALL": "Called tool",
    "ACTION_PROPOSED": "Proposed action",
    "POLICY_DECISION": "Policy decision",
    "APPROVAL_REQUESTED": "Requested approval",
    "APPROVAL_GRANTED": "Approved action",
    "APPROVAL_REJECTED": "Rejected action",
    "APPROVAL_CHANGES_REQUESTED": "Requested changes",
    "ACTION_EXECUTED": "Executed action",
    "ACTION_FAILED": "Action failed",
    "CONNECTOR_RESPONSE": "Connector response",
    "WORKFLOW_STARTED": "Started workflow",
    "WORKFLOW_COMPLETED": "Completed workflow",
    "WORKFLOW_FAILED": "Workflow failed",
    "CONFIG_CHANGED": "Changed configuration",
    "HUMAN_REVIEW_CREATED": "Created review",
    "HUMAN_REVIEW_RESOLVED": "Resolved review",
    "GUARDRAIL_TRIGGERED": "Guardrail triggered",
    "MEMORY_CONFLICT": "Memory conflict",
    "CHANGE_ORDER": "Change order",
    "DATA_SCOPES_CHANGED": "Changed data scopes",
    "AUDIT_PURGED": "Purged audit log",
}


def _summary(row: dict[str, Any]) -> str:
    """One-line description of what happened for the Audit table."""
    event_type = str(row.get("event_type") or "")
    label = _EVENT_LABELS.get(event_type) or event_type.replace("_", " ").title() or "Event"
    parts = [label]
    tool = row.get("tool")
    if tool:
        parts.append(str(tool))
    decision = row.get("decision")
    if decision:
        parts.append(str(decision))
    elif row.get("status") and event_type in {
        "TOOL_CALL",
        "ACTION_EXECUTED",
        "ACTION_FAILED",
        "POLICY_DECISION",
        "WORKFLOW_COMPLETED",
        "WORKFLOW_FAILED",
    }:
        parts.append(str(row["status"]))
    return " · ".join(parts)


def _enrich(rows: list[dict[str, Any]], ctx: TenantContext) -> list[dict[str, Any]]:
    """Attach display name + summary so the UI can show who did what."""
    from freedombot.control import store as control

    try:
        users = {u.id: u for u in control.list_users(ctx.client_id)}
    except Exception:
        users = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        user = users.get(row.get("user_id") or "")
        item["user_name"] = (user.display_name or user.email) if user else None
        item["summary"] = _summary(row)
        out.append(item)
    return out


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
        return {
            "items": _enrich(rows, ctx),
            "count": len(rows),
            "total": audit.count(ctx),
        }


@router.get("/event-types")
def event_types() -> dict[str, list[str]]:
    """The vocabulary, for the UI's filter dropdown."""
    return {"event_types": [str(e) for e in AuditEventType]}


@router.get("/retention")
def retention(ctx: TenantContext = Depends(_reader)) -> dict[str, Any]:
    """The audit retention rule in force for this workspace."""
    from freedombot.control import store as control

    days = control.get_client(ctx.client_id).settings.retention_policy.audit_days
    return {
        "audit_days": days,
        "effective_days": max(days, audit.MIN_AUDIT_RETENTION_DAYS) if days > 0 else 0,
        "minimum_days": audit.MIN_AUDIT_RETENTION_DAYS,
    }


@router.post("/purge")
def purge(ctx: TenantContext = Depends(require_role(Role.ADMIN))) -> dict[str, Any]:
    """Apply the retention rule now: delete only rows older than the window."""
    from freedombot.control import store as control

    days = control.get_client(ctx.client_id).settings.retention_policy.audit_days
    with bound(ctx):
        return audit.purge_expired(days, ctx=ctx)


@router.get("/token-usage")
def token_usage(
    days: int = Query(default=30, ge=1, le=365),
    user_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=1000),
    ctx: TenantContext = Depends(require_role(Role.ADMIN)),
) -> dict[str, Any]:
    """Tokens and estimated cost per chat query in this workspace. Admins only."""
    from freedombot import usage
    from freedombot.control import store as control

    with bound(ctx):
        data = usage.report(ctx, days=days, user_id=user_id or None, limit=limit)
    names = {u.id: (u.display_name or u.email) for u in control.list_users(ctx.client_id)}
    for row in (*data["by_user"], *data["items"]):
        row["user_name"] = names.get(row["user_id"], row["user_id"])
    return data
