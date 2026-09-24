"""
Audit Log
=========

Structured, append-only security auditing (§9).

Three properties are enforced rather than requested:

1. **Append-only.** A database trigger rejects UPDATE and DELETE on
   ``audit_events``, including from the table's owner, so an attacker who reaches
   the application database still cannot rewrite history.
2. **Redacted.** Every ``request``/``result`` payload passes through
   :func:`bizos.util.redaction.redact` on the way in. Passwords, OAuth tokens,
   API keys, bearer headers, database URLs and card numbers never land in a row.
3. **Never fatal.** ``record`` swallows its own failures. An audit write that
   raised would let a broken log block a legitimate business action — or, worse,
   become a denial-of-service lever. Failures are logged to stderr instead.

Writes go to the *client's own workspace*, so an audit trail is as isolated as
the data it describes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from agno.utils.log import log_error
from sqlalchemy import text

from bizos.tenancy.context import TenantContext, current_context
from bizos.tenancy.registry import workspace_connection
from bizos.types import AuditEventType
from bizos.util.ids import new_id
from bizos.util.redaction import redact


@dataclass
class AuditEvent:
    """One structured audit record."""

    event_type: AuditEventType
    client_id: str
    user_id: Optional[str] = None
    actor_role: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    action_id: Optional[str] = None
    tool: Optional[str] = None
    integration: Optional[str] = None
    domain: Optional[str] = None
    decision: Optional[str] = None
    status: Optional[str] = None
    approver: Optional[str] = None
    risk_level: Optional[str] = None
    classification: Optional[str] = None
    request: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None
    #: The execution mode that governed this event (FB-038).
    execution_mode: Optional[str] = None
    id: str = field(default_factory=lambda: new_id("aud"))


_INSERT = text(
    """
    INSERT INTO audit_events (
        id, event_type, client_id, user_id, actor_role, agent_id, session_id, run_id,
        workflow_id, workflow_run_id, action_id, tool, integration, domain, decision,
        status, approver, risk_level, classification, request, result, error, latency_ms,
        execution_mode
    ) VALUES (
        :id, :event_type, :client_id, :user_id, :actor_role, :agent_id, :session_id, :run_id,
        :workflow_id, :workflow_run_id, :action_id, :tool, :integration, :domain, :decision,
        :status, :approver, :risk_level, :classification,
        CAST(:request AS JSONB), CAST(:result AS JSONB), :error, :latency_ms,
        :execution_mode
    )
    """
)


def _payload(value: Optional[dict[str, Any]]) -> Optional[str]:
    """Redact and serialize a JSONB payload."""
    if value is None:
        return None
    return json.dumps(redact(value), default=str)


def record(event: AuditEvent, *, ctx: Optional[TenantContext] = None) -> Optional[str]:
    """Write one audit event. Returns its id, or ``None`` if the write failed.

    Never raises: auditing must not be able to break the operation it describes.
    """
    tenant = ctx or current_context()
    try:
        with workspace_connection(tenant) as conn:
            conn.execute(
                _INSERT,
                {
                    "id": event.id,
                    "event_type": str(event.event_type),
                    "client_id": event.client_id,
                    "user_id": event.user_id,
                    "actor_role": event.actor_role,
                    "agent_id": event.agent_id,
                    "session_id": event.session_id,
                    "run_id": event.run_id,
                    "workflow_id": event.workflow_id,
                    "workflow_run_id": event.workflow_run_id,
                    "action_id": event.action_id,
                    "tool": event.tool,
                    "integration": event.integration,
                    "domain": event.domain,
                    "decision": event.decision,
                    "status": event.status,
                    "approver": event.approver,
                    "risk_level": event.risk_level,
                    "classification": event.classification,
                    "request": _payload(event.request),
                    "result": _payload(event.result),
                    "error": event.error,
                    "latency_ms": event.latency_ms,
                    "execution_mode": event.execution_mode,
                },
            )
        return event.id
    except Exception as exc:  # never fatal
        log_error(f"audit write failed ({event.event_type}): {exc}")
        return None


def log(
    event_type: AuditEventType,
    *,
    ctx: Optional[TenantContext] = None,
    **fields: Any,
) -> Optional[str]:
    """Convenience wrapper that fills client/user/role from the tenant context."""
    tenant = ctx or current_context()
    if tenant is None:
        log_error(f"audit: dropping {event_type} — no tenant context bound")
        return None
    fields.setdefault("client_id", tenant.client_id)
    fields.setdefault("user_id", tenant.user_id)
    fields.setdefault("actor_role", str(tenant.primary_role))
    if not fields.get("execution_mode"):
        request = fields.get("request")
        mode = request.get("mode") if isinstance(request, dict) else None
        fields["execution_mode"] = str(mode or tenant.default_execution_mode)
    return record(AuditEvent(event_type=event_type, **fields), ctx=tenant)


def log_policy_decision(decision: Any, *, ctx: Optional[TenantContext] = None, **extra: Any) -> Optional[str]:
    """Record a policy verdict with its full constraint set."""
    return log(
        AuditEventType.POLICY_DECISION,
        ctx=ctx,
        tool=decision.tool,
        decision=str(decision.effect),
        risk_level=str(decision.risk),
        classification=str(decision.classification),
        status=decision.rule,
        execution_mode=str(decision.mode),
        request={"mode": str(decision.mode), **extra},
        result={"reason": decision.reason, "constraints": [c.code for c in decision.constraints]},
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

#: Columns the audit search may filter and sort on. An allowlist, because these
#: are interpolated into the ORDER BY clause.
_SORTABLE = frozenset({"created_at", "seq", "event_type", "user_id", "status"})


def search(
    *,
    ctx: Optional[TenantContext] = None,
    event_types: Optional[list[str]] = None,
    user_id: Optional[str] = None,
    tool: Optional[str] = None,
    integration: Optional[str] = None,
    workflow_id: Optional[str] = None,
    action_id: Optional[str] = None,
    status: Optional[str] = None,
    decision: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "created_at",
    descending: bool = True,
) -> list[dict[str, Any]]:
    """Filtered audit search for the Audit UI (§17)."""
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 500)), "offset": max(0, offset)}
    if event_types:
        clauses.append("event_type = ANY(:event_types)")
        params["event_types"] = event_types
    for column, value in (
        ("user_id", user_id),
        ("tool", tool),
        ("integration", integration),
        ("workflow_id", workflow_id),
        ("action_id", action_id),
        ("status", status),
        ("decision", decision),
    ):
        if value:
            clauses.append(f"{column} = :{column}")
            params[column] = value
    if date_from:
        clauses.append("created_at >= CAST(:date_from AS TIMESTAMPTZ)")
        params["date_from"] = date_from
    if date_to:
        clauses.append("created_at <= CAST(:date_to AS TIMESTAMPTZ)")
        params["date_to"] = date_to
    if query:
        clauses.append(
            "(event_type ILIKE :q OR COALESCE(tool,'') ILIKE :q OR COALESCE(error,'') ILIKE :q "
            "OR COALESCE(result::text,'') ILIKE :q)"
        )
        params["q"] = f"%{query}%"

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_column = sort if sort in _SORTABLE else "created_at"
    direction = "DESC" if descending else "ASC"

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                f"SELECT * FROM audit_events {where} "
                f"ORDER BY {order_column} {direction}, seq {direction} "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        ).fetchall()
    return [dict(r._mapping) for r in rows]


def count(ctx: Optional[TenantContext] = None) -> int:
    with workspace_connection(ctx, readonly=True) as conn:
        return int(conn.execute(text("SELECT count(*) FROM audit_events")).scalar() or 0)


#: Floor on the audit retention window. A misconfigured "1 day" must not be
#: able to erase the trail of last week's approvals.
MIN_AUDIT_RETENTION_DAYS = 30


def purge_expired(
    retention_days: int,
    *,
    ctx: Optional[TenantContext] = None,
    now: Optional[Any] = None,
) -> dict[str, Any]:
    """Apply the audit retention rule (FB-038).

    Deletes audit rows older than ``retention_days`` — the only deletion the
    append-only trigger permits, and only inside this transaction. ``0`` keeps
    everything. The purge itself is recorded as a new audit event.
    """
    from datetime import datetime, timedelta, timezone

    tenant = ctx or current_context()
    if retention_days <= 0:
        return {"deleted": 0, "cutoff": None, "retention_days": retention_days}
    days = max(int(retention_days), MIN_AUDIT_RETENTION_DAYS)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    with workspace_connection(tenant) as conn:
        conn.execute(
            text("SELECT set_config('bizos.audit_purge_before', :c, true)"),
            {"c": cutoff.isoformat()},
        )
        deleted = conn.execute(
            text("DELETE FROM audit_events WHERE created_at < CAST(:c AS TIMESTAMPTZ)"),
            {"c": cutoff.isoformat()},
        ).rowcount
    log(
        AuditEventType.AUDIT_PURGED,
        ctx=tenant,
        request={"retention_days": days, "cutoff": cutoff.isoformat()},
        result={"deleted": deleted},
    )
    return {"deleted": deleted, "cutoff": cutoff.isoformat(), "retention_days": days}
