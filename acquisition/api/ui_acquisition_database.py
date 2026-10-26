"""Acquisition owner / Admin read-only permanent prospect database + learning Admin."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from acquisition.auth.admin import (
    is_acquisition_owner,
    is_admin_authenticated,
    require_acquisition_reader_html_request,
    require_admin_html_request,
)
from acquisition.db.session import get_db
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
@router.post(
    "/settings/ai/learning-recompute",
    response_model=None,
    dependencies=[Depends(require_admin_html_request)],
)
def learning_retired():
    return RedirectResponse(url="/settings/ai?flash=engine_retired", status_code=status.HTTP_303_SEE_OTHER)
