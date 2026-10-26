"""Admin Overview dashboard. Live Trace / old discovery runs are retired."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_html_request
from acquisition.db.session import get_db
from acquisition.services.acquisition_live_service import SOURCE_LABELS, AcquisitionLiveService
from acquisition.templating import create_templates

templates = create_templates()
router = APIRouter(tags=["acquisition-live"])

_ENGINE_RETIRED = "The old discovery engine is retired. Use Prospecting rules."


def _retired_live() -> RedirectResponse:
    return RedirectResponse(url="/overview", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/overview", response_class=HTMLResponse, dependencies=[Depends(require_admin_html_request)])
def overview_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    svc = AcquisitionLiveService(db)
    data = svc.overview()
    return templates.TemplateResponse(
        request,
        "acquisition/overview.html",
        {
            "data": data,
            "sources": [],
            "source_labels": SOURCE_LABELS,
            "nav_live_running": False,
        },
    )


@router.get("/acquisition/live", response_class=HTMLResponse, dependencies=[Depends(require_admin_html_request)])
def live_index() -> RedirectResponse:
    return _retired_live()


@router.post("/acquisition/live/run", dependencies=[Depends(require_admin_html_request)])
@router.post("/acquisition/live/preview", dependencies=[Depends(require_admin_html_request)])
def live_start_retired() -> RedirectResponse:
    return _retired_live()


@router.get(
    "/acquisition/live/{run_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_html_request)],
)
def live_run(run_id: uuid.UUID) -> RedirectResponse:
    del run_id
    return _retired_live()


@router.get("/acquisition/live/{run_id}/events.json", dependencies=[Depends(require_admin_html_request)])
def live_events(run_id: uuid.UUID) -> JSONResponse:
    del run_id
    return JSONResponse({"error": _ENGINE_RETIRED}, status_code=410)


@router.post(
    "/acquisition/live/{run_id}/replay-selection",
    dependencies=[Depends(require_admin_html_request)],
)
def live_replay_selection(run_id: uuid.UUID) -> RedirectResponse:
    del run_id
    return _retired_live()


@router.post("/settings/ai/discovery-sources", dependencies=[Depends(require_admin_html_request)])
async def save_discovery_sources(request: Request) -> RedirectResponse:
    del request
    return RedirectResponse(url="/settings/ai?flash=engine_retired", status_code=status.HTTP_303_SEE_OTHER)
