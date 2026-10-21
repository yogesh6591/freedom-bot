"""Campaign admin UI routes (Jinja2 + HTMX)."""

from __future__ import annotations

import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.models.campaign import CampaignStatus, MessageType
from acquisition.models.prospect import ApprovalStatus
from acquisition.schemas.campaign import (
    CampaignCreate,
    CampaignMessageCreate,
    CampaignMessageUpdate,
    CampaignUpdate,
)
from acquisition.services.campaign_activation_service import CampaignActivationService
from acquisition.services.campaign_service import (
    CampaignMessageNotFoundError,
    CampaignNotFoundError,
    CampaignService,
    CampaignValidationError,
)
from acquisition.services.instantly_campaign_sync_service import (
    InstantlyCampaignSyncService,
    InstantlySyncError,
)
from acquisition.services.lead_sync_service import LeadSyncError, LeadSyncService
from acquisition.services.prospect_service import ProspectService
from acquisition.integrations.instantly.mapper import InstantlyMappingError, build_instantly_campaign_preview
from acquisition.services.campaign_activation_service import ActivationError
from acquisition.templating import create_templates
from acquisition.auth.admin import require_admin_html_request

templates = create_templates()

router = APIRouter(tags=["admin-ui-campaigns"], dependencies=[Depends(require_admin_html_request)])


def get_campaign_service(db: Session = Depends(get_db)) -> CampaignService:
    return CampaignService(db)


def get_prospect_service(db: Session = Depends(get_db)) -> ProspectService:
    return ProspectService(db)


def get_sync_service(db: Session = Depends(get_db)) -> InstantlyCampaignSyncService:
    return InstantlyCampaignSyncService(db)


_FIELD_LABELS = {
    "name": "Campaign name",
    "sender_name": "Sender name",
    "assessment_url": "Assessment URL",
    "send_timezone": "Timezone",
    "send_window_start": "Send window start",
    "send_window_end": "Send window end",
}


def _friendly_validation_msg(err: dict) -> str:
    raw = str(err.get("msg") or "Invalid value").strip()
    if raw.startswith("Value error, "):
        raw = raw[len("Value error, ") :]
    loc = err.get("loc") or ()
    field = str(loc[-1]) if loc else ""
    label = _FIELD_LABELS.get(field, field.replace("_", " ").capitalize() if field else "")
    low = raw.lower()
    if "at least 1 character" in low or "field required" in low:
        return f"{label or 'This field'} is required."
    if label and not raw.lower().startswith(label.lower()):
        return f"{label}: {raw}"
    return raw


def _validation_messages(
    exc: ValidationError | CampaignValidationError | ValueError,
) -> list[str]:
    if isinstance(exc, ValueError):
        return [str(exc) or "Invalid value"]
    if isinstance(exc, CampaignValidationError):
        if exc.missing:
            return [exc.message, *exc.missing]
        return [exc.message]
    messages = []
    for err in exc.errors():
        messages.append(_friendly_validation_msg(err))
    return messages or ["Please fix the highlighted fields."]


def _blank(value: str | None) -> bool:
    return not (value or "").strip()


def _delay_label(hours: int) -> str:
    if hours == 0:
        return "Send immediately"
    if hours % 24 == 0 and hours >= 24:
        days = hours // 24
        return f"Wait {days} day{'s' if days != 1 else ''} ({hours} hours)"
    return f"Wait {hours} hours"


_VALID_TABS = frozenset({"details", "schedule", "sequence", "customers", "launch"})
_FLASH_TO_TAB = {
    "created": "details",
    "updated": "details",
    "details_error": "details",
    "name_required": "details",
    "sender_required": "details",
    "schedule_updated": "schedule",
    "message_added": "sequence",
    "message_updated": "sequence",
    "message_deleted": "sequence",
    "message_error": "sequence",
    "prospect_added": "customers",
    "prospect_removed": "customers",
    "prospect_error": "customers",
    "synced": "launch",
    "sync_error": "launch",
    "synced_leads_error": "launch",
    "leads_synced": "customers",
    "lead_synced": "customers",
    "leads_sync_error": "customers",
    "accounts_saved": "launch",
    "accounts_error": "launch",
    "activated": "launch",
    "activate_error": "launch",
    "paused": "launch",
    "pause_error": "launch",
}


def _resolve_tab(tab: str | None, flash: str | None) -> str:
    if tab and tab in _VALID_TABS:
        return tab
    if flash and flash in _FLASH_TO_TAB:
        return _FLASH_TO_TAB[flash]
    return "details"


def _campaign_url(
    campaign_id: uuid.UUID,
    *,
    flash: str | None = None,
    tab: str | None = None,
) -> str:
    resolved = _resolve_tab(tab, flash)
    parts = [f"tab={resolved}"]
    if flash:
        parts.append(f"flash={flash}")
    return f"/campaigns/{campaign_id}?{'&'.join(parts)}"


def _detail_context(
    *,
    campaign,
    service: CampaignService,
    prospect_service: ProspectService,
    prospect_search: str = "",
    errors: list[str] | None = None,
    flash: str | None = None,
    tab: str | None = None,
) -> dict:
    attached_ids = {link.prospect_id for link in campaign.campaign_prospects}
    approved, _ = prospect_service.list_prospects(
        approval_status=ApprovalStatus.APPROVED,
        search=prospect_search or None,
        page=1,
        page_size=100,
    )
    available = [p for p in approved if p.id not in attached_ids]
    preview = None
    preview_error = None
    try:
        preview = build_instantly_campaign_preview(campaign)
    except InstantlyMappingError as exc:
        preview_error = exc.message

    activation = CampaignActivationService(service.db)
    selected_emails = activation.get_selected_emails(campaign)
    sending_accounts = []
    activation_checklist: list[dict] = []
    try:
        sending_accounts = [
            {"email": a.email, "status": a.status} for a in activation.list_sending_accounts()
        ]
    except ActivationError:
        sending_accounts = []
    try:
        activation_checklist = activation.activation_checklist(campaign.id)
    except Exception:  # noqa: BLE001
        activation_checklist = []

    from acquisition.time_format import to_12h_parts
    from acquisition.integrations.instantly.timezones import (
        INSTANTLY_TIMEZONE_SET,
        normalize_instantly_timezone,
        timezone_choices,
    )

    ready_checklist = service.ready_checklist(campaign)
    ready_remaining = sum(1 for item in ready_checklist if not item["done"])
    activation_remaining = sum(1 for item in activation_checklist if not item["done"])
    launch_total = len(activation_checklist) or 1
    launch_done = launch_total - activation_remaining
    active_tab = _resolve_tab(tab, flash)
    window_start_12h = to_12h_parts(campaign.send_window_start)
    window_end_12h = to_12h_parts(campaign.send_window_end)
    clock_minutes = ["00", "15", "30", "45"]
    for minute in (window_start_12h["minute"], window_end_12h["minute"]):
        if minute not in clock_minutes:
            clock_minutes.append(minute)
    clock_minutes = sorted(set(clock_minutes))
    selected_timezone = normalize_instantly_timezone(campaign.send_timezone)
    if selected_timezone not in INSTANTLY_TIMEZONE_SET:
        selected_timezone = "America/Chicago"

    return {
        "campaign": campaign,
        "messages": sorted(campaign.messages, key=lambda m: m.sequence_order),
        "attached": campaign.campaign_prospects,
        "available_prospects": available,
        "prospect_search": prospect_search or "",
        "errors": errors or [],
        "ready_checklist": ready_checklist,
        "ready_remaining": ready_remaining,
        "delay_label": _delay_label,
        "flash": flash,
        "active_tab": active_tab,
        "MessageType": MessageType,
        "CampaignStatus": CampaignStatus,
        "instantly_preview": preview,
        "instantly_preview_error": preview_error,
        "sending_accounts": sending_accounts,
        "selected_emails": selected_emails,
        "activation_checklist": activation_checklist,
        "activation_remaining": activation_remaining,
        "launch_done": launch_done,
        "launch_total": launch_total,
        "launch_pct": int(round((launch_done / launch_total) * 100)),
        "activation_blockers": [
            item["detail"] or item["label"]
            for item in activation_checklist
            if not item["done"]
        ],
        "window_start_12h": window_start_12h,
        "window_end_12h": window_end_12h,
        "clock_hours": list(range(1, 13)),
        "clock_minutes": clock_minutes,
        "timezone_choices": timezone_choices(),
        "selected_timezone": selected_timezone,
    }


@router.get("/campaigns", response_class=HTMLResponse)
def campaign_list(
    request: Request,
    search: str | None = Query(default=None),
    status_filter: CampaignStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    service: CampaignService = Depends(get_campaign_service),
) -> HTMLResponse:
    settings = get_settings()
    page_size = settings.default_page_size
    rows, total = service.list_campaigns(
        search=search,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    campaigns = [
        {
            "campaign": campaign,
            "message_count": message_count,
            "prospect_count": prospect_count,
        }
        for campaign, message_count, prospect_count in rows
    ]
    return templates.TemplateResponse(
        request,
        "campaigns/list.html",
        {
            "campaigns": campaigns,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "search": search or "",
            "status": status_filter.value if status_filter else "",
            "flash": request.query_params.get("flash"),
        },
    )


@router.get("/campaigns/new", response_class=HTMLResponse)
def campaign_new(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "campaigns/form.html",
        {
            "mode": "create",
            "form_data": {
                "name": "",
                "sender_name": settings.default_sender_name,
            },
            "errors": [],
        },
    )


@router.post("/campaigns/new", response_class=HTMLResponse, response_model=None)
def campaign_create(
    request: Request,
    name: Annotated[str, Form()] = "",
    sender_name: Annotated[str, Form()] = "",
    service: CampaignService = Depends(get_campaign_service),
):
    form_data = {
        "name": name,
        "sender_name": sender_name,
    }
    if _blank(name):
        return templates.TemplateResponse(
            request,
            "campaigns/form.html",
            {
                "mode": "create",
                "form_data": form_data,
                "errors": ["Campaign name is required."],
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        payload = CampaignCreate.model_validate(
            {
                "name": name,
                "sender_name": sender_name or None,
                "seed_default_sequence": True,
            }
        )
        campaign = service.create_campaign(payload)
        return RedirectResponse(
            url=_campaign_url(campaign.id, flash="created"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValidationError, CampaignValidationError) as exc:
        return templates.TemplateResponse(
            request,
            "campaigns/form.html",
            {
                "mode": "create",
                "form_data": form_data,
                "errors": _validation_messages(exc),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse, response_model=None)
def campaign_detail(
    request: Request,
    campaign_id: uuid.UUID,
    prospect_search: str | None = Query(default=None),
    tab: str | None = Query(default=None),
    service: CampaignService = Depends(get_campaign_service),
    prospect_service: ProspectService = Depends(get_prospect_service),
):
    try:
        campaign = service.get_campaign(campaign_id)
    except CampaignNotFoundError:
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return templates.TemplateResponse(
        request,
        "campaigns/detail.html",
        _detail_context(
            campaign=campaign,
            service=service,
            prospect_service=prospect_service,
            prospect_search=prospect_search or "",
            flash=request.query_params.get("flash"),
            tab=tab,
        ),
    )


@router.post("/campaigns/{campaign_id}", response_class=HTMLResponse, response_model=None)
def campaign_update(
    request: Request,
    campaign_id: uuid.UUID,
    name: Annotated[str, Form()] = "",
    sender_name: Annotated[str, Form()] = "",
    assessment_url: Annotated[str, Form()] = "",
    status_value: Annotated[str, Form()] = "draft",
    return_tab: Annotated[str, Form()] = "details",
    service: CampaignService = Depends(get_campaign_service),
    prospect_service: ProspectService = Depends(get_prospect_service),
):
    if _blank(name):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="name_required", tab=return_tab or "details"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if _blank(sender_name):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="sender_required", tab=return_tab or "details"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        # Assessment URL is system-managed (PUBLIC_BASE_URL + per-lead tokens).
        # Launch tab may still post the stored value; empty means "keep / auto-fill".
        update_data: dict = {
            "name": name.strip(),
            "sender_name": sender_name.strip(),
            "status": status_value,
        }
        if assessment_url.strip():
            update_data["assessment_url"] = assessment_url
        payload = CampaignUpdate.model_validate(update_data)
        campaign = service.update_campaign(campaign_id, payload)
        return RedirectResponse(
            url=_campaign_url(campaign.id, flash="updated", tab=return_tab),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except CampaignNotFoundError:
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValidationError, CampaignValidationError):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="details_error", tab=return_tab or "details"),
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/schedule", response_class=HTMLResponse, response_model=None)
def campaign_schedule_update(
    request: Request,
    campaign_id: uuid.UUID,
    send_timezone: Annotated[str, Form()] = "America/Chicago",
    send_window_start_hour: Annotated[str, Form()] = "12",
    send_window_start_minute: Annotated[str, Form()] = "00",
    send_window_start_period: Annotated[str, Form()] = "AM",
    send_window_end_hour: Annotated[str, Form()] = "10",
    send_window_end_minute: Annotated[str, Form()] = "30",
    send_window_end_period: Annotated[str, Form()] = "AM",
    send_monday: Annotated[str | None, Form()] = None,
    send_tuesday: Annotated[str | None, Form()] = None,
    send_wednesday: Annotated[str | None, Form()] = None,
    send_thursday: Annotated[str | None, Form()] = None,
    send_friday: Annotated[str | None, Form()] = None,
    send_saturday: Annotated[str | None, Form()] = None,
    send_sunday: Annotated[str | None, Form()] = None,
    service: CampaignService = Depends(get_campaign_service),
    prospect_service: ProspectService = Depends(get_prospect_service),
):
    from acquisition.time_format import from_12h_parts
    from acquisition.integrations.instantly.timezones import (
        INSTANTLY_TIMEZONE_SET,
        normalize_instantly_timezone,
    )

    try:
        send_window_start = from_12h_parts(
            send_window_start_hour,
            send_window_start_minute,
            send_window_start_period,
        )
        send_window_end = from_12h_parts(
            send_window_end_hour,
            send_window_end_minute,
            send_window_end_period,
        )
        timezone = normalize_instantly_timezone(send_timezone)
        if timezone not in INSTANTLY_TIMEZONE_SET:
            raise ValueError(
                "Timezone is not supported by Instantly. Choose India (Kolkata) or another listed zone."
            )
        payload = CampaignUpdate.model_validate(
            {
                "send_timezone": timezone,
                "send_window_start": send_window_start,
                "send_window_end": send_window_end,
                "send_monday": send_monday == "true",
                "send_tuesday": send_tuesday == "true",
                "send_wednesday": send_wednesday == "true",
                "send_thursday": send_thursday == "true",
                "send_friday": send_friday == "true",
                "send_saturday": send_saturday == "true",
                "send_sunday": send_sunday == "true",
            }
        )
        campaign = service.update_campaign(campaign_id, payload)
        return RedirectResponse(
            url=_campaign_url(campaign.id, flash="schedule_updated"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except CampaignNotFoundError:
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (ValidationError, CampaignValidationError, ValueError) as exc:
        try:
            campaign = service.get_campaign(campaign_id)
        except CampaignNotFoundError:
            return RedirectResponse(
                url="/campaigns?flash=not_found",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return templates.TemplateResponse(
            request,
            "campaigns/detail.html",
            _detail_context(
                campaign=campaign,
                service=service,
                prospect_service=prospect_service,
                errors=_validation_messages(exc),
            ),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.post("/campaigns/{campaign_id}/instantly/sync", response_model=None)
def campaign_instantly_sync(
    campaign_id: uuid.UUID,
    sync_service: InstantlyCampaignSyncService = Depends(get_sync_service),
    db: Session = Depends(get_db),
):
    """Sync campaign definition to Instantly, then push attached prospects as leads."""
    try:
        sync_service.sync(campaign_id)
    except CampaignNotFoundError:
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except InstantlySyncError:
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="sync_error"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        LeadSyncService(db).sync_campaign_leads(campaign_id)
    except LeadSyncError:
        # Campaign definition already landed; check Lead sync status under Prospects.
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="synced_leads_error"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=_campaign_url(campaign_id, flash="synced"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/campaigns/{campaign_id}/delete", response_model=None)
def campaign_delete(
    campaign_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
):
    try:
        service.delete_campaign(campaign_id)
        return RedirectResponse(
            url="/campaigns?flash=deleted",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except CampaignNotFoundError:
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except CampaignValidationError:
        return RedirectResponse(
            url="/campaigns?flash=delete_instantly_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/messages", response_model=None)
def campaign_add_message(
    campaign_id: uuid.UUID,
    message_type: Annotated[str, Form()] = "follow_up",
    subject: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    delay_after_previous_hours: Annotated[int, Form()] = 0,
    is_active: Annotated[str, Form()] = "true",
    service: CampaignService = Depends(get_campaign_service),
):
    try:
        payload = CampaignMessageCreate.model_validate(
            {
                "message_type": message_type,
                "subject": subject or None,
                "body": body or None,
                "delay_after_previous_hours": max(0, delay_after_previous_hours),
                "is_active": is_active.lower() in {"1", "true", "on", "yes"},
            }
        )
        service.create_message(campaign_id, payload)
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="message_added"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, CampaignValidationError, ValidationError):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="message_error"),
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/messages/{message_id}", response_model=None)
def campaign_update_message(
    campaign_id: uuid.UUID,
    message_id: uuid.UUID,
    sequence_order: Annotated[int, Form()] = 1,
    message_type: Annotated[str, Form()] = "follow_up",
    subject: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    delay_after_previous_hours: Annotated[int, Form()] = 0,
    is_active: Annotated[str, Form()] = "false",
    service: CampaignService = Depends(get_campaign_service),
):
    try:
        payload = CampaignMessageUpdate.model_validate(
            {
                "sequence_order": max(1, sequence_order),
                "message_type": message_type,
                "subject": subject or None,
                "body": body or None,
                "delay_after_previous_hours": max(0, delay_after_previous_hours),
                "is_active": is_active.lower() in {"1", "true", "on", "yes"},
            }
        )
        service.update_message(campaign_id, message_id, payload)
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="message_updated"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, CampaignMessageNotFoundError):
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignValidationError, ValidationError):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="message_error"),
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/messages/{message_id}/delete", response_model=None)
def campaign_delete_message(
    campaign_id: uuid.UUID,
    message_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
):
    try:
        service.delete_message(campaign_id, message_id)
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="message_deleted"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, CampaignMessageNotFoundError):
        return RedirectResponse(
            url="/campaigns?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/prospects", response_model=None)
def campaign_attach_prospect(
    campaign_id: uuid.UUID,
    prospect_id: Annotated[str, Form()],
    service: CampaignService = Depends(get_campaign_service),
):
    try:
        service.attach_prospect(campaign_id, uuid.UUID(prospect_id))
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="prospect_added"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, CampaignValidationError, ValueError):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="prospect_error"),
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/campaigns/{campaign_id}/prospects/{prospect_id}/delete", response_model=None)
def campaign_detach_prospect(
    campaign_id: uuid.UUID,
    prospect_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
):
    try:
        service.detach_prospect(campaign_id, prospect_id)
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="prospect_removed"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except (CampaignNotFoundError, CampaignValidationError):
        return RedirectResponse(
            url=_campaign_url(campaign_id, flash="prospect_error"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
