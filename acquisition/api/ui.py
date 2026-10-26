"""Server-rendered admin UI routes (Jinja2 + HTMX)."""

from __future__ import annotations

import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_html_request
from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.models.prospect import ApprovalStatus
from acquisition.schemas.prospect import ProspectCreate, ProspectUpdate
from acquisition.services.prospect_import import ProspectImportError, parse_prospect_upload
from acquisition.services.prospect_service import ProspectNotFoundError, ProspectService
from acquisition.templating import create_templates

templates = create_templates()

router = APIRouter(tags=["admin-ui"], dependencies=[Depends(require_admin_html_request)])


def get_prospect_service(db: Session = Depends(get_db)) -> ProspectService:
    return ProspectService(db)


def _linkedin_export_filename() -> str:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).date().isoformat()
    return f"freedombot_linkedin_prospects_{stamp}.csv"


def _form_payload(
    *,
    company: str,
    website: str,
    linkedin_url: str,
    executive_name: str,
    executive_title: str,
    email: str,
    revenue_band: str,
    headcount_band: str,
    fit_note: str,
    source: str,
    approval_status: str,
) -> dict:
    return {
        "company": company,
        "website": website or None,
        "linkedin_url": linkedin_url or None,
        "executive_name": executive_name,
        "executive_title": executive_title or None,
        "email": email,
        "revenue_band": revenue_band or None,
        "headcount_band": headcount_band or None,
        "fit_note": fit_note or None,
        "source": source or None,
        "approval_status": approval_status or ApprovalStatus.PENDING.value,
    }


def _validation_errors(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err.get("loc", ()))
        messages.append(f"{field}: {err.get('msg', 'Invalid value')}")
    return messages


def _empty_form() -> dict[str, str]:
    return {
        "company": "",
        "website": "",
        "linkedin_url": "",
        "executive_name": "",
        "executive_title": "",
        "email": "",
        "revenue_band": "",
        "headcount_band": "",
        "fit_note": "",
        "source": "",
        "approval_status": ApprovalStatus.PENDING.value,
    }


def _static_prospect_template() -> dict[str, str]:
    """Development static template for Add Prospect form."""
    return {
        "company": "ABC Consulting",
        "website": "",
        "linkedin_url": "",
        "executive_name": "Sarah Miller",
        "executive_title": "CEO",
        "email": "johndoe.sys07@gmail.com",
        "revenue_band": "$10–25M",
        "headcount_band": "50-100",
        "fit_note": "",
        "source": "ListKit",
        "approval_status": ApprovalStatus.PENDING.value,
    }


@router.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(url="/overview", status_code=status.HTTP_302_FOUND)


def _prospect_list_context(
    *,
    request: Request,
    service: ProspectService,
    search: str | None = None,
    tab: str = "inbox",
    source: str | None = None,
    page: int = 1,
    import_errors: list[str] | None = None,
    open_import_modal: bool = False,
) -> dict:
    settings = get_settings()
    page_size = settings.default_page_size
    active_tab = (tab or "inbox").strip().lower()
    if active_tab not in {"inbox", "approved"}:
        active_tab = "inbox"

    if active_tab == "approved":
        items, total = service.list_prospects(
            search=search,
            approval_status=ApprovalStatus.APPROVED,
            source=source,
            page=page,
            page_size=page_size,
        )
    else:
        items, total = service.list_prospects(
            search=search,
            exclude_approved=True,
            source=source,
            page=page,
            page_size=page_size,
        )

    counts = service.count_by_approval()
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    open_modal = open_import_modal or request.query_params.get("import") == "1"
    return {
        "prospects": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "search": search or "",
        "tab": active_tab,
        "source": source or "",
        "sources": service.distinct_sources(),
        "counts": counts,
        "flash": request.query_params.get("flash"),
        "flash_count": request.query_params.get("count"),
        "import_errors": import_errors or [],
        "open_import_modal": open_modal,
    }


@router.get("/prospects", response_class=HTMLResponse)
def prospect_list(
    request: Request,
    search: str | None = Query(default=None),
    tab: str = Query(default="inbox"),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    service: ProspectService = Depends(get_prospect_service),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "prospects/list.html",
        _prospect_list_context(
            request=request,
            service=service,
            search=search,
            tab=tab,
            source=source,
            page=page,
        ),
    )


@router.get("/prospects/new", response_class=HTMLResponse)
def prospect_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "prospects/form.html",
        {
            "mode": "create",
            "prospect": None,
            "form_data": _static_prospect_template(),
            "errors": [],
            "using_static_template": True,
        },
    )


@router.get("/prospects/import")
def prospect_import_page() -> RedirectResponse:
    return RedirectResponse(
        url="/prospects?tab=inbox&import=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/prospects/import", response_class=HTMLResponse, response_model=None)
async def prospect_import_upload(
    request: Request,
    file: UploadFile = File(...),
    service: ProspectService = Depends(get_prospect_service),
) -> HTMLResponse | RedirectResponse:
    filename = file.filename or ""
    content = await file.read()

    def _error_page(errors: list[str]) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "prospects/list.html",
            _prospect_list_context(
                request=request,
                service=service,
                tab="inbox",
                import_errors=errors,
                open_import_modal=True,
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        rows = parse_prospect_upload(filename=filename, content=content)
    except ProspectImportError as exc:
        return _error_page([exc.message])

    result = service.import_prospects_from_rows(rows)
    created = int(result.get("created") or 0)
    error_count = int(result.get("error_count") or 0)
    row_errors = list(result.get("errors") or [])
    if created == 0 and error_count:
        return _error_page(row_errors or ["No prospects were imported."])
    return RedirectResponse(
        url=(
            f"/prospects?tab=inbox&flash=imported&count={created}"
            + (f"&errors={error_count}" if error_count else "")
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _parse_prospect_ids(form) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for value in form.getlist("prospect_ids"):
        try:
            ids.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return ids


@router.post("/prospects/bulk-approve")
async def prospect_bulk_approve(
    request: Request,
    service: ProspectService = Depends(get_prospect_service),
) -> RedirectResponse:
    form = await request.form()
    updated = service.bulk_approve(_parse_prospect_ids(form))
    return RedirectResponse(
        url=f"/prospects?tab=approved&flash=bulk_approved&count={updated}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/prospects/bulk-unapprove")
async def prospect_bulk_unapprove(
    request: Request,
    service: ProspectService = Depends(get_prospect_service),
) -> RedirectResponse:
    form = await request.form()
    updated = service.bulk_unapprove(_parse_prospect_ids(form))
    return RedirectResponse(
        url=f"/prospects?tab=inbox&flash=bulk_unapproved&count={updated}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/prospects/bulk-delete")
async def prospect_bulk_delete(
    request: Request,
    service: ProspectService = Depends(get_prospect_service),
) -> RedirectResponse:
    form = await request.form()
    deleted = service.bulk_delete(_parse_prospect_ids(form))
    tab = str(form.get("tab") or "inbox").strip().lower()
    if tab not in {"inbox", "approved"}:
        tab = "inbox"
    return RedirectResponse(
        url=f"/prospects?tab={tab}&flash=bulk_deleted&count={deleted}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/prospects/export/linkedin")
def export_linkedin_prospects(
    service: ProspectService = Depends(get_prospect_service),
) -> Response:
    csv_body = service.build_linkedin_export_csv()
    filename = _linkedin_export_filename()
    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/prospects/export/structured")
def export_structured_prospects(
    service: ProspectService = Depends(get_prospect_service),
) -> Response:
    from datetime import datetime, timezone

    csv_body = service.build_structured_export_csv()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"prospects-structured-{stamp}.csv"
    return Response(
        content=csv_body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/prospects/new", response_class=HTMLResponse, response_model=None)
def prospect_create(
    request: Request,
    company: Annotated[str, Form()] = "",
    website: Annotated[str, Form()] = "",
    linkedin_url: Annotated[str, Form()] = "",
    executive_name: Annotated[str, Form()] = "",
    executive_title: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    revenue_band: Annotated[str, Form()] = "",
    headcount_band: Annotated[str, Form()] = "",
    fit_note: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "",
    approval_status: Annotated[str, Form()] = ApprovalStatus.PENDING.value,
    service: ProspectService = Depends(get_prospect_service),
) -> HTMLResponse | RedirectResponse:
    # New prospects always enter the inbox as pending; approval is bulk/UI tab driven.
    approval_status = ApprovalStatus.PENDING.value
    form_data = {
        "company": company,
        "website": website,
        "linkedin_url": linkedin_url,
        "executive_name": executive_name,
        "executive_title": executive_title,
        "email": email,
        "revenue_band": revenue_band,
        "headcount_band": headcount_band,
        "fit_note": fit_note,
        "source": source,
        "approval_status": approval_status,
    }
    try:
        payload = ProspectCreate.model_validate(
            _form_payload(
                company=company,
                website=website,
                linkedin_url=linkedin_url,
                executive_name=executive_name,
                executive_title=executive_title,
                email=email,
                revenue_band=revenue_band,
                headcount_band=headcount_band,
                fit_note=fit_note,
                source=source,
                approval_status=approval_status,
            )
        )
        service.create_prospect(payload)
        return RedirectResponse(
            url="/prospects?tab=inbox&flash=created",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "prospects/form.html",
            {
                "mode": "create",
                "prospect": None,
                "form_data": form_data,
                "errors": _validation_errors(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.get("/prospects/{prospect_id}", response_class=HTMLResponse, response_model=None)
def prospect_detail(
    request: Request,
    prospect_id: uuid.UUID,
    service: ProspectService = Depends(get_prospect_service),
) -> HTMLResponse | RedirectResponse:
    try:
        prospect = service.get_prospect(prospect_id)
    except ProspectNotFoundError:
        return RedirectResponse(url="/prospects?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    form_data = {
        "company": prospect.company or "",
        "website": prospect.website or "",
        "linkedin_url": prospect.linkedin_url or "",
        "executive_name": prospect.executive_name or "",
        "executive_title": prospect.executive_title or "",
        "email": prospect.email or "",
        "revenue_band": prospect.revenue_band or "",
        "headcount_band": prospect.headcount_band or "",
        "fit_note": prospect.fit_note or "",
        "source": prospect.source or "",
        "approval_status": prospect.approval_status.value,
    }
    return templates.TemplateResponse(
        request,
        "prospects/detail.html",
        {
            "mode": "edit",
            "prospect": prospect,
            "form_data": form_data,
            "errors": [],
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/prospects/{prospect_id}", response_class=HTMLResponse, response_model=None)
def prospect_update(
    request: Request,
    prospect_id: uuid.UUID,
    company: Annotated[str, Form()] = "",
    website: Annotated[str, Form()] = "",
    linkedin_url: Annotated[str, Form()] = "",
    executive_name: Annotated[str, Form()] = "",
    executive_title: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    revenue_band: Annotated[str, Form()] = "",
    headcount_band: Annotated[str, Form()] = "",
    fit_note: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "",
    approval_status: Annotated[str, Form()] = ApprovalStatus.PENDING.value,
    service: ProspectService = Depends(get_prospect_service),
) -> HTMLResponse | RedirectResponse:
    try:
        prospect = service.get_prospect(prospect_id)
    except ProspectNotFoundError:
        return RedirectResponse(url="/prospects?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    form_data = {
        "company": company,
        "website": website,
        "linkedin_url": linkedin_url,
        "executive_name": executive_name,
        "executive_title": executive_title,
        "email": email,
        "revenue_band": revenue_band,
        "headcount_band": headcount_band,
        "fit_note": fit_note,
        "source": source,
        "approval_status": approval_status,
    }
    try:
        payload = ProspectUpdate.model_validate(
            _form_payload(
                company=company,
                website=website,
                linkedin_url=linkedin_url,
                executive_name=executive_name,
                executive_title=executive_title,
                email=email,
                revenue_band=revenue_band,
                headcount_band=headcount_band,
                fit_note=fit_note,
                source=source,
                approval_status=approval_status,
            )
        )
        prospect = service.update_prospect(prospect_id, payload)
        tab = (
            "approved"
            if prospect.approval_status == ApprovalStatus.APPROVED
            else "inbox"
        )
        return RedirectResponse(
            url=f"/prospects?tab={tab}&flash=updated",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "prospects/detail.html",
            {
                "mode": "edit",
                "prospect": prospect,
                "form_data": form_data,
                "errors": _validation_errors(exc),
                "flash": None,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.post("/prospects/{prospect_id}/delete")
def prospect_delete(
    prospect_id: uuid.UUID,
    service: ProspectService = Depends(get_prospect_service),
) -> RedirectResponse:
    try:
        service.delete_prospect(prospect_id)
        return RedirectResponse(
            url="/prospects?tab=inbox&flash=deleted",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ProspectNotFoundError:
        return RedirectResponse(
            url="/prospects?tab=inbox&flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/prospects/{prospect_id}/research")
def prospect_research_and_score(
    prospect_id: uuid.UUID,
) -> RedirectResponse:
    """Old-engine research path is retired."""
    return RedirectResponse(
        url=f"/prospects/{prospect_id}?flash=engine_retired",
        status_code=status.HTTP_303_SEE_OTHER,
    )
