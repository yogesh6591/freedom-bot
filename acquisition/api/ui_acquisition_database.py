"""Acquisition owner / Admin read-only permanent prospect database + learning Admin."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from acquisition.auth.admin import (
    is_acquisition_owner,
    is_admin_authenticated,
    require_acquisition_reader_html_request,
    require_admin_html_request,
)
from acquisition.db.session import get_db
from acquisition.services.acquisition_learning_service import AcquisitionLearningService
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.prospect_database_service import (
    ProspectDatabaseFilters,
    ProspectDatabaseService,
)
from acquisition.templating import create_templates

templates = create_templates()
router = APIRouter(tags=["acquisition-database"])


def _filters_from_request(request: Request) -> ProspectDatabaseFilters:
    qp = request.query_params

    def _float(name: str) -> float | None:
        raw = (qp.get(name) or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    return ProspectDatabaseFilters(
        acquisition_status=(qp.get("acquisition_status") or "").strip() or None,
        industry=(qp.get("industry") or "").strip() or None,
        state=(qp.get("state") or "").strip() or None,
        region=(qp.get("region") or "").strip() or None,
        outreach_status=(qp.get("outreach_status") or "").strip() or None,
        assessment_status=(qp.get("assessment_status") or "").strip() or None,
        qualification_status=(qp.get("qualification_status") or "").strip() or None,
        meeting_status=(qp.get("meeting_status") or "").strip() or None,
        purchase_status=(qp.get("purchase_status") or "").strip() or None,
        min_priority=_float("min_priority"),
        max_priority=_float("max_priority"),
        q=(qp.get("q") or "").strip() or None,
    )


@router.get(
    "/acquisition/database",
    response_class=HTMLResponse,
    dependencies=[Depends(require_acquisition_reader_html_request)],
)
def acquisition_database_list(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    filters = _filters_from_request(request)
    try:
        page = max(1, int(request.query_params.get("page") or "1"))
    except ValueError:
        page = 1
    limit = 50
    offset = (page - 1) * limit
    rows, total = ProspectDatabaseService(db).list_enriched(
        filters=filters, limit=limit, offset=offset
    )
    return templates.TemplateResponse(
        request,
        "acquisition/database_list.html",
        {
            "prospects": rows,
            "total": total,
            "page": page,
            "pages": max(1, (total + limit - 1) // limit),
            "filters": filters,
            "is_owner": is_acquisition_owner(request),
            "is_admin": is_admin_authenticated(request),
            "flash": request.query_params.get("flash"),
        },
    )


@router.get(
    "/acquisition/database/export.csv",
    dependencies=[Depends(require_acquisition_reader_html_request)],
)
def export_csv(request: Request, db: Session = Depends(get_db)) -> Response:
    filters = _filters_from_request(request)
    body = ProspectDatabaseService(db).build_csv(filters=filters)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="freedombot-prospects-{stamp}.csv"'
        },
    )


@router.get(
    "/acquisition/database/export.xlsx",
    dependencies=[Depends(require_acquisition_reader_html_request)],
)
def export_xlsx(request: Request, db: Session = Depends(get_db)) -> Response:
    filters = _filters_from_request(request)
    body = ProspectDatabaseService(db).build_xlsx_bytes(filters=filters)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="freedombot-prospects-{stamp}.xlsx"'
        },
    )


@router.get(
    "/acquisition/database/{prospect_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_acquisition_reader_html_request)],
)
def acquisition_database_detail(
    prospect_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    row = ProspectDatabaseService(db).get_enriched(prospect_id)
    if row is None:
        return RedirectResponse(
            url="/acquisition/database?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return templates.TemplateResponse(
        request,
        "acquisition/database_detail.html",
        {
            "prospect": row,
            "is_owner": is_acquisition_owner(request),
            "is_admin": is_admin_authenticated(request),
        },
    )


@router.post(
    "/settings/ai/learning-config",
    response_model=None,
    dependencies=[Depends(require_admin_html_request)],
)
def learning_config_save(
    learning_enabled: Annotated[str | None, Form()] = None,
    learning_min_sample_size: Annotated[str, Form()] = "25",
    learning_max_adjustment: Annotated[str, Form()] = "8",
    learning_model: Annotated[str, Form()] = "gpt-4o-mini",
    db: Session = Depends(get_db),
):
    try:
        min_n = int((learning_min_sample_size or "25").strip() or "25")
    except ValueError:
        min_n = 25
    try:
        max_adj = float((learning_max_adjustment or "8").strip() or "8")
    except ValueError:
        max_adj = 8.0
    from acquisition.services.ai_outcome_learning_service import ALLOWED_MODELS

    model = (learning_model or "gpt-4o-mini").strip() or "gpt-4o-mini"
    if model not in ALLOWED_MODELS:
        model = "gpt-4o-mini"
    IcpSettingsService(db).update(
        learning_enabled=learning_enabled == "true",
        learning_min_sample_size=min_n,
        learning_max_adjustment=max_adj,
        learning_model=model,
    )
    return RedirectResponse(
        url="/settings/ai?flash=learning_saved",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/settings/ai/learning-recompute",
    response_model=None,
    dependencies=[Depends(require_admin_html_request)],
)
def learning_recompute(db: Session = Depends(get_db)):
    result = AcquisitionLearningService(db).refresh_all_prospect_adjustments()
    summary = (
        f"updated={result.get('updated')} "
        f"openai_calls={result.get('openai_calls')} "
        f"profile_v={result.get('learning_profile_version')}"
    )
    return RedirectResponse(
        url=f"/settings/ai?flash=learning_recomputed&summary={quote(summary)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
