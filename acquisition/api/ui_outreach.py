"""Admin page: every email we have sent or queued, plus a stop button."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_html_request
from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.models.outreach import STATUS_FAILED, STATUS_QUEUED, STATUS_SENDING, STATUS_SENT, SentEmail
from acquisition.models.prospect import Prospect
from acquisition.services import outreach_service as svc
from acquisition.services.outreach_settings import effective
from acquisition.templating import create_templates

templates = create_templates()

router = APIRouter(tags=["admin-ui-outreach"], dependencies=[Depends(require_admin_html_request)])

_PAGE_SIZE = 50
_FILTERS = {"queued": (STATUS_QUEUED, STATUS_SENDING), "sent": (STATUS_SENT,), "failed": (STATUS_FAILED,)}


@router.get("/outreach", response_class=HTMLResponse)
def sent_emails_page(
    request: Request,
    state: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    counts = {
        row[0]: int(row[1])
        for row in db.execute(select(SentEmail.status, func.count()).group_by(SentEmail.status))
    }
    waiting = counts.get(STATUS_QUEUED, 0) + counts.get(STATUS_SENDING, 0)
    today = svc.sent_today(db)

    query = select(SentEmail, Prospect).join(Prospect, Prospect.id == SentEmail.prospect_id)
    wanted = _FILTERS.get((state or "").strip().lower())
    if wanted:
        query = query.where(SentEmail.status.in_(wanted))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(SentEmail.queued_at.desc()).offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
    ).all()

    cfg = get_settings()
    eff = effective(db)
    return templates.TemplateResponse(
        request,
        "outreach/list.html",
        {
            "rows": [{"email": e, "prospect": p} for e, p in rows],
            "total": total,
            "page": page,
            "page_size": _PAGE_SIZE,
            "state": (state or "").strip().lower(),
            "waiting": waiting,
            "sent_total": counts.get(STATUS_SENT, 0),
            "failed": counts.get(STATUS_FAILED, 0),
            "sent_today": today,
            "daily_ceiling": cfg.outreach_daily_ceiling,
            "eta_minutes": svc.eta_minutes(waiting, today) if waiting else 0,
            "worker_on": eff.worker_enabled,
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/outreach/stop", response_model=None)
def stop_sending(db: Session = Depends(get_db)) -> RedirectResponse:
    """Cancel every email that has not gone out yet."""
    svc.cancel_all_queued(db)
    db.commit()
    return RedirectResponse(url="/outreach?flash=stopped", status_code=status.HTTP_303_SEE_OTHER)
