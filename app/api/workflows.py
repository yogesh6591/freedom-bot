"""Workflow routes (§26 /api/workflows, /api/workflow-runs)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import bound, client_settings, current_context, require_admin, require_operator, run_scope
from bizos.control.models import ClientSettings
from bizos.tenancy.context import TenantContext
from bizos.workflows import runner
from bizos.workflows.playbooks import run_playbook
from bizos.workflows.templates import TEMPLATES_BY_ID, WORKFLOW_TEMPLATES

router = APIRouter(prefix="/api", tags=["workflows"])


class WorkflowUpdate(BaseModel):
    enabled: Optional[bool] = None
    execution_mode: Optional[str] = None
    cron: Optional[str] = None
    recipients: Optional[list[str]] = None
    connector_config: Optional[dict[str, Any]] = None
    approval_policy: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    description: Optional[str] = None


class RunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    #: May only narrow the workspace mode, never widen it.
    execution_mode: Optional[str] = None


@router.get("/workflows")
def list_workflows(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """Client workflow configuration joined to the template catalogue."""
    with bound(ctx):
        configs = {c["id"]: c for c in runner.list_configs(ctx)}
        items = []
        for template in WORKFLOW_TEMPLATES:
            config = configs.get(template.id, {})
            items.append({**template.to_dict(), "config": config})
        return {"items": items}


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    template = TEMPLATES_BY_ID.get(workflow_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Unknown workflow {workflow_id}")
    with bound(ctx):
        return {
            **template.to_dict(),
            "config": runner.get_config(workflow_id, ctx=ctx) or {},
            "runs": runner.list_runs(workflow_id=workflow_id, limit=20, ctx=ctx),
        }


@router.patch("/workflows/{workflow_id}")
def update_workflow(
    workflow_id: str, body: WorkflowUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    """Enable/disable, reschedule, set mode, recipients and connector config."""
    if workflow_id not in TEMPLATES_BY_ID:
        raise HTTPException(status_code=404, detail=f"Unknown workflow {workflow_id}")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    with bound(ctx):
        return runner.update_config(workflow_id, updates=updates, ctx=ctx)


@router.post("/workflows/{workflow_id}/run")
def run_workflow(
    workflow_id: str,
    body: RunRequest,
    ctx: TenantContext = Depends(require_operator),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Run a playbook now.

    Runs synchronously so the caller sees the real outcome — including whether a
    step landed in the approval queue — rather than a fire-and-forget id.
    """
    if workflow_id not in TEMPLATES_BY_ID:
        raise HTTPException(status_code=404, detail=f"Unknown workflow {workflow_id}")
    template = TEMPLATES_BY_ID[workflow_id]
    missing = [key for key in template.required_inputs if not str(body.inputs.get(key, "")).strip()]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{workflow_id} requires inputs: {', '.join(template.required_inputs)}. "
                f"Missing: {', '.join(missing)}. "
                'Example: {"email": "lead@example.com", "name": "Alex", "message": "pricing please"}'
            ),
        )
    with bound(ctx):
        config = runner.get_config(workflow_id, ctx=ctx) or {}
        if config and not config.get("enabled", True):
            raise HTTPException(status_code=409, detail=f"{workflow_id} is disabled for this workspace")
        scope = run_scope(
            ctx,
            settings,
            requested_mode=body.execution_mode or config.get("execution_mode"),
        )
        run = run_playbook(scope, workflow_id, inputs=body.inputs, trigger="manual")
        return run.to_dict()


@router.get("/workflow-runs")
def list_runs(
    workflow_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    ctx: TenantContext = Depends(current_context),
) -> dict[str, Any]:
    with bound(ctx):
        return {"items": runner.list_runs(workflow_id=workflow_id, limit=limit, ctx=ctx)}
