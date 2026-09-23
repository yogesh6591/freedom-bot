"""
Workflow Runner
===============

Run bookkeeping for the business playbooks: creates a ``workflow_runs`` row,
records each step into ``workflow_steps``, and audits start/completion/failure.

Steps are ordinary Python functions that call
:func:`bizos.tools.base.guarded_call`, so a workflow has **no more authority than
the user or schedule that triggered it**. A step whose write exceeds the client's
policy produces an approval request and the workflow reports that, rather than
executing it because "it's automation".

A step may also *pause* the workflow by raising :class:`NeedsHumanInput`, which
records a human review item and leaves the run in ``AWAITING_REVIEW``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy import text

from bizos.audit import events as audit
from bizos.tenancy.context import TenantContext, require_context
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.tools.base import RunScope
from bizos.types import AuditEventType
from bizos.util.ids import new_id

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_AWAITING_APPROVAL = "AWAITING_APPROVAL"
STATUS_AWAITING_REVIEW = "AWAITING_REVIEW"


class NeedsHumanInput(Exception):
    """A step cannot proceed without a human answer.

    Carries the review item so the runner can pause the workflow rather than
    guessing — the §16 behavior, expressed as control flow.
    """

    def __init__(self, review_id: str, question: str) -> None:
        super().__init__(question)
        self.review_id = review_id
        self.question = question


@dataclass
class StepResult:
    """What one step produced."""

    name: str
    ok: bool
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    action_id: Optional[str] = None
    #: True when the step produced an approval request instead of acting.
    awaiting_approval: bool = False
    error: Optional[str] = None


@dataclass
class WorkflowRun:
    id: str
    workflow_id: str
    template: str
    status: str
    steps: list[StepResult] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "template": self.template,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "steps": [
                {
                    "name": s.name,
                    "ok": s.ok,
                    "summary": s.summary,
                    "output": s.output,
                    "action_id": s.action_id,
                    "awaiting_approval": s.awaiting_approval,
                    "error": s.error,
                }
                for s in self.steps
            ],
        }


Step = Callable[[RunScope, dict[str, Any], "WorkflowState"], StepResult]


@dataclass
class WorkflowState:
    """Mutable state carried between steps of one run."""

    run_id: str
    data: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)

    def set(self, key: str, value: Any) -> Any:
        self.data[key] = value
        return value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def get_config(workflow_id: str, *, ctx: Optional[TenantContext] = None) -> Optional[dict[str, Any]]:
    with readonly_connection(ctx) as conn:
        row = conn.execute(
            text("SELECT * FROM workflow_configs WHERE id = :i"), {"i": workflow_id}
        ).first()
    return dict(row._mapping) if row else None


def list_configs(ctx: Optional[TenantContext] = None) -> list[dict[str, Any]]:
    with readonly_connection(ctx) as conn:
        rows = conn.execute(text("SELECT * FROM workflow_configs ORDER BY name")).fetchall()
    return [dict(r._mapping) for r in rows]


def update_config(
    workflow_id: str, *, updates: dict[str, Any], ctx: Optional[TenantContext] = None
) -> dict[str, Any]:
    """Update a client's customization of a workflow template."""
    tenant = ctx or require_context()
    allowed = {"enabled", "execution_mode", "cron", "recipients", "connector_config", "approval_policy", "name", "description"}
    sets, params = [], {"i": workflow_id, "u": tenant.user_id}
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key in {"connector_config", "approval_policy"}:
            sets.append(f"{key} = CAST(:{key} AS JSONB)")
            params[key] = json.dumps(value, default=str)
        else:
            sets.append(f"{key} = :{key}")
            params[key] = value
    if not sets:
        return get_config(workflow_id, ctx=tenant) or {}
    with workspace_connection(tenant) as conn:
        conn.execute(
            text(f"UPDATE workflow_configs SET {', '.join(sets)}, updated_by = :u, updated_at = NOW() WHERE id = :i"),
            params,
        )
    audit.log(
        AuditEventType.CONFIG_CHANGED,
        ctx=tenant,
        workflow_id=workflow_id,
        request={"updates": updates},
        status="OK",
    )
    return get_config(workflow_id, ctx=tenant) or {}


def list_runs(
    *, workflow_id: Optional[str] = None, limit: int = 50, ctx: Optional[TenantContext] = None
) -> list[dict[str, Any]]:
    clauses, params = [], {"limit": max(1, min(limit, 200))}
    if workflow_id:
        clauses.append("workflow_id = :w")
        params["w"] = workflow_id
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with readonly_connection(ctx) as conn:
        rows = conn.execute(
            text(f"SELECT * FROM workflow_runs {where} ORDER BY started_at DESC LIMIT :limit"), params
        ).fetchall()
        runs = []
        for row in rows:
            steps = conn.execute(
                text("SELECT * FROM workflow_steps WHERE workflow_run_id = :r ORDER BY step_index"),
                {"r": row.id},
            ).fetchall()
            entry = dict(row._mapping)
            entry["steps"] = [dict(s._mapping) for s in steps]
            runs.append(entry)
    return runs


def execute(
    scope: RunScope,
    *,
    workflow_id: str,
    template: str,
    steps: list[tuple[str, Step]],
    inputs: Optional[dict[str, Any]] = None,
    trigger: str = "manual",
) -> WorkflowRun:
    """Run a playbook, recording every step. Never raises for a step failure."""
    tenant = scope.ctx
    run_id = new_id("wfr")
    inputs = dict(inputs or {})
    scope.workflow_id = workflow_id
    scope.workflow_run_id = run_id

    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_runs (id, workflow_id, template, status, trigger, "
                "triggered_by, input) VALUES (:i, :w, :t, :s, :tr, :u, CAST(:in AS JSONB))"
            ),
            {
                "i": run_id,
                "w": workflow_id,
                "t": template,
                "s": STATUS_RUNNING,
                "tr": trigger,
                "u": tenant.user_id,
                "in": json.dumps(inputs, default=str),
            },
        )
    audit.log(
        AuditEventType.WORKFLOW_STARTED,
        ctx=tenant,
        workflow_id=workflow_id,
        workflow_run_id=run_id,
        request={"inputs": inputs, "trigger": trigger},
        status=STATUS_RUNNING,
    )

    state = WorkflowState(run_id=run_id)
    run = WorkflowRun(id=run_id, workflow_id=workflow_id, template=template, status=STATUS_RUNNING)
    final_status = STATUS_COMPLETED
    error: Optional[str] = None

    for index, (name, step) in enumerate(steps):
        started = time.monotonic()
        try:
            result = step(scope, inputs, state)
        except NeedsHumanInput as pause:
            result = StepResult(
                name=name,
                ok=False,
                summary=f"paused for human input: {pause.question}",
                output={"review_id": pause.review_id},
            )
            _record_step(tenant, run_id, index, result, started)
            run.steps.append(result)
            final_status = STATUS_AWAITING_REVIEW
            error = f"awaiting human review {pause.review_id}"
            break
        except Exception as exc:
            result = StepResult(name=name, ok=False, summary=f"step failed: {exc}", error=str(exc))
            _record_step(tenant, run_id, index, result, started)
            run.steps.append(result)
            final_status = STATUS_FAILED
            error = f"{name}: {exc}"
            break

        _record_step(tenant, run_id, index, result, started)
        run.steps.append(result)
        if result.action_id:
            state.actions.append(result.action_id)
        if result.awaiting_approval:
            final_status = STATUS_AWAITING_APPROVAL
        if not result.ok and not result.awaiting_approval:
            final_status = STATUS_FAILED
            error = f"{name}: {result.error or result.summary}"
            break

    run.status = final_status
    run.error = error
    run.output = dict(state.data)
    run.output["actions"] = state.actions

    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "UPDATE workflow_runs SET status = :s, output = CAST(:o AS JSONB), error = :e, "
                "finished_at = NOW() WHERE id = :i"
            ),
            {"s": final_status, "o": json.dumps(run.output, default=str), "e": error, "i": run_id},
        )
    audit.log(
        AuditEventType.WORKFLOW_COMPLETED if final_status == STATUS_COMPLETED else AuditEventType.WORKFLOW_FAILED,
        ctx=tenant,
        workflow_id=workflow_id,
        workflow_run_id=run_id,
        status=final_status,
        result={"steps": len(run.steps), "actions": state.actions},
        error=error,
    )
    return run


def _record_step(
    tenant: TenantContext, run_id: str, index: int, result: StepResult, started: float
) -> None:
    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_steps (workflow_run_id, step_index, name, status, summary, "
                "output, error, action_id, finished_at) VALUES "
                "(:r, :i, :n, :s, :su, CAST(:o AS JSONB), :e, :a, NOW())"
            ),
            {
                "r": run_id,
                "i": index,
                "n": result.name,
                "s": "OK" if result.ok else ("AWAITING_APPROVAL" if result.awaiting_approval else "FAILED"),
                "su": result.summary,
                "o": json.dumps(result.output, default=str),
                "e": result.error,
                "a": result.action_id,
            },
        )
