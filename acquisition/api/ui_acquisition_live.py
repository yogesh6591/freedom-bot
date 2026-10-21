"""Admin Overview dashboard + Live Trace (step-by-step pipeline view)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_html_request
from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.services.acquisition_live_service import SOURCE_LABELS, AcquisitionLiveService
from acquisition.services.live_trace import STAGES
from acquisition.templating import create_templates

templates = create_templates()
router = APIRouter(tags=["acquisition-live"])


def _source_status(db: Session) -> list[dict]:
    from acquisition.integrations.discovery.factory import resolve_discovery_provider

    provider = resolve_discovery_provider(get_settings(), db=db)
    fn = getattr(provider, "source_status", None)
    return fn() if callable(fn) else []


@router.get("/overview", response_class=HTMLResponse, dependencies=[Depends(require_admin_html_request)])
def overview_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    svc = AcquisitionLiveService(db)
    data = svc.overview()
    return templates.TemplateResponse(
        request,
        "acquisition/overview.html",
        {
            "data": data,
            "sources": _source_status(db),
            "source_labels": SOURCE_LABELS,
            "nav_live_running": data["running"],
        },
    )


@router.get("/acquisition/live", response_class=HTMLResponse, dependencies=[Depends(require_admin_html_request)])
def live_index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    svc = AcquisitionLiveService(db)
    runs = svc.recent_runs(limit=30)
    if runs:
        return RedirectResponse(url=f"/acquisition/live/{runs[0]['id']}", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request,
        "acquisition/live.html",
        {
            "runs": [],
            "run": None,
            "stages": STAGES,
            "sources": _source_status(db),
            "flash": request.query_params.get("flash"),
            "nav_live_running": False,
        },
    )


@router.post("/acquisition/live/run", dependencies=[Depends(require_admin_html_request)])
def live_start_run(db: Session = Depends(get_db)) -> RedirectResponse:
    svc = AcquisitionLiveService(db)
    if svc.is_running():
        runs = svc.recent_runs(limit=1)
        target = f"/acquisition/live/{runs[0]['id']}?flash=already_running" if runs else "/acquisition/live"
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    run_id = svc.start_discovery_in_background()
    return RedirectResponse(url=f"/acquisition/live/{run_id}?flash=started", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/acquisition/live/preview", dependencies=[Depends(require_admin_html_request)])
def live_start_preview(db: Session = Depends(get_db)) -> RedirectResponse:
    svc = AcquisitionLiveService(db)
    if svc.is_running():
        runs = svc.recent_runs(limit=1)
        target = f"/acquisition/live/{runs[0]['id']}?flash=already_running" if runs else "/acquisition/live"
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    run_id = svc.start_discovery_preview_in_background()
    return RedirectResponse(
        url=f"/acquisition/live/{run_id}?flash=preview_started",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/acquisition/live/{run_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_html_request)],
)
def live_run(run_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    svc = AcquisitionLiveService(db)
    run = svc.run(run_id)
    if run is None:
        return RedirectResponse(url="/acquisition/live", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request,
        "acquisition/live.html",
        {
            "runs": svc.recent_runs(limit=30),
            "run": run,
            "stages": STAGES,
            "stage_counts": svc.stage_counts(run_id),
            "sources": _source_status(db),
            "flash": request.query_params.get("flash"),
            "nav_live_running": svc.is_running(),
        },
    )


@router.get("/acquisition/live/{run_id}/events.json", dependencies=[Depends(require_admin_html_request)])
def live_events(run_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    try:
        after = max(0, int(request.query_params.get("after") or 0))
    except ValueError:
        after = 0
    svc = AcquisitionLiveService(db)
    run = svc.run(run_id)
    if run is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(
        {
            "run": run,
            "events": svc.events(run_id, after=after, for_ui=True),
            "stage_counts": svc.stage_counts(run_id),
            "story": svc.story(run_id),
        }
    )


@router.post(
    "/acquisition/live/{run_id}/replay-selection",
    dependencies=[Depends(require_admin_html_request)],
)
def live_replay_selection(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    svc = AcquisitionLiveService(db)
    if svc.run(run_id) is None:
        return RedirectResponse(url="/acquisition/live", status_code=status.HTTP_302_FOUND)
    if svc.is_running():
        return RedirectResponse(
            url=f"/acquisition/live/{run_id}?flash=already_running",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    result = svc.replay_ai_selection(run_id)
    if not result.get("ok"):
        # Surface reconstruction failure on the source run.
        return RedirectResponse(
            url=f"/acquisition/live/{run_id}?flash=replay_pool_unavailable",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/acquisition/live/{result['replay_run_id']}?flash=replay_done",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/ai/discovery-sources", dependencies=[Depends(require_admin_html_request)])
async def save_discovery_sources(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    from acquisition.integrations.discovery.multi_source import (
        DEFAULT_FETCH_LIMITS,
        SUPPORTED_SOURCES,
        clamp_fetch_limit,
    )
    from acquisition.services.icp_settings_service import IcpSettingsService

    form = await request.form()
    cfg = {}
    for name in SUPPORTED_SOURCES:
        raw = form.get(f"{name}_fetch_limit")
        if raw is None or str(raw).strip() == "":
            fetch_limit = DEFAULT_FETCH_LIMITS[name]
        else:
            fetch_limit = clamp_fetch_limit(name, raw)
        cfg[name] = {
            "enabled": form.get(f"{name}_enabled") in {"true", "on", "1"},
            "fetch_limit": fetch_limit,
        }
    # OpenAI web is the only source — keep it enabled if the checkbox was left off.
    if not any(v.get("enabled") for v in cfg.values()):
        cfg["openai_web"]["enabled"] = True
    IcpSettingsService(db).update(discovery_sources=cfg)
    return RedirectResponse(url="/settings/ai?flash=sources_saved#engine", status_code=status.HTTP_303_SEE_OTHER)
