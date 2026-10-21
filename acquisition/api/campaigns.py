"""Campaign REST API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_api
from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.models.campaign import CampaignStatus
from acquisition.schemas.campaign import (
    CampaignCreate,
    CampaignDetail,
    CampaignListResponse,
    CampaignMessageCreate,
    CampaignMessageRead,
    CampaignMessageUpdate,
    CampaignProspectAttach,
    CampaignProspectRead,
    CampaignSummary,
    CampaignUpdate,
    InstantlyCampaignPreviewResponse,
    InstantlyPreviewStep,
    InstantlySyncResponse,
)
from acquisition.schemas.prospect import ProspectRead
from acquisition.models.campaign import InstantlyRuntimeStatus, InstantlySyncStatus  # noqa: F401 — re-export use in summary
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

router = APIRouter(
    prefix="/api/campaigns",
    tags=["campaigns"],
    dependencies=[Depends(require_admin_api)],
)


def get_campaign_service(db: Session = Depends(get_db)) -> CampaignService:
    return CampaignService(db)


def get_sync_service(db: Session = Depends(get_db)) -> InstantlyCampaignSyncService:
    return InstantlyCampaignSyncService(db)


def _http_from_validation(exc: CampaignValidationError) -> HTTPException:
    detail: dict | str
    if exc.missing:
        detail = {"message": exc.message, "missing": exc.missing}
    else:
        detail = exc.message
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _to_detail(campaign) -> CampaignDetail:
    import json

    from acquisition.services.assessment_tokens import build_assessment_url

    prospects = []
    for link in campaign.campaign_prospects:
        assessment_url = None
        if link.assessment_token:
            assessment_url = build_assessment_url(link.assessment_token)
        prospects.append(
            CampaignProspectRead(
                id=link.id,
                campaign_id=link.campaign_id,
                prospect_id=link.prospect_id,
                created_at=link.created_at,
                instantly_lead_id=link.instantly_lead_id,
                sync_status=link.sync_status,
                outreach_status=link.outreach_status,
                last_synced_at=link.last_synced_at,
                last_activity_at=link.last_activity_at,
                last_reply_at=link.last_reply_at,
                assessment_status=link.assessment_status,
                cold_status=link.cold_status,
                assessment_token=link.assessment_token,
                sync_error=link.sync_error,
                assessment_started_at=link.assessment_started_at,
                assessment_completed_at=link.assessment_completed_at,
                assessment_url=assessment_url,
                prospect=ProspectRead.model_validate(link.prospect) if link.prospect else None,
            )
        )
    messages = sorted(campaign.messages, key=lambda m: m.sequence_order)
    emails: list[str] = []
    if campaign.instantly_email_list:
        try:
            raw = json.loads(campaign.instantly_email_list)
            if isinstance(raw, list):
                emails = [str(x) for x in raw]
        except json.JSONDecodeError:
            emails = []
    return CampaignDetail(
        id=campaign.id,
        name=campaign.name,
        sender_name=campaign.sender_name,
        assessment_url=campaign.assessment_url,
        status=campaign.status,
        send_timezone=campaign.send_timezone,
        send_window_start=campaign.send_window_start,
        send_window_end=campaign.send_window_end,
        send_monday=campaign.send_monday,
        send_tuesday=campaign.send_tuesday,
        send_wednesday=campaign.send_wednesday,
        send_thursday=campaign.send_thursday,
        send_friday=campaign.send_friday,
        send_saturday=campaign.send_saturday,
        send_sunday=campaign.send_sunday,
        instantly_campaign_id=campaign.instantly_campaign_id,
        instantly_sync_status=campaign.instantly_sync_status,
        instantly_last_synced_at=campaign.instantly_last_synced_at,
        instantly_sync_error=campaign.instantly_sync_error,
        instantly_email_list=emails,
        instantly_runtime_status=campaign.instantly_runtime_status,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        messages=[CampaignMessageRead.model_validate(m) for m in messages],
        prospects=prospects,
    )


@router.get("", response_model=CampaignListResponse)
def list_campaigns(
    search: str | None = Query(default=None),
    status_filter: CampaignStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignListResponse:
    settings = get_settings()
    size = min(page_size or settings.default_page_size, settings.max_page_size)
    rows, total = service.list_campaigns(
        search=search,
        status=status_filter,
        page=page,
        page_size=size,
    )
    items = [
        CampaignSummary(
            id=campaign.id,
            name=campaign.name,
            sender_name=campaign.sender_name,
            assessment_url=campaign.assessment_url,
            status=campaign.status,
            instantly_sync_status=campaign.instantly_sync_status,
            instantly_campaign_id=campaign.instantly_campaign_id,
            instantly_runtime_status=campaign.instantly_runtime_status,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
            message_count=message_count,
            prospect_count=prospect_count,
        )
        for campaign, message_count, prospect_count in rows
    ]
    return CampaignListResponse(items=items, total=total, page=page, page_size=size)


@router.post("", response_model=CampaignDetail, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignCreate,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignDetail:
    try:
        campaign = service.create_campaign(payload)
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc
    return _to_detail(campaign)


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(
    campaign_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignDetail:
    try:
        campaign = service.get_campaign(campaign_id)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_detail(campaign)


@router.patch("/{campaign_id}", response_model=CampaignDetail)
def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignDetail:
    try:
        campaign = service.update_campaign(campaign_id, payload)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc
    return _to_detail(campaign)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_campaign(
    campaign_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
) -> None:
    try:
        service.delete_campaign(campaign_id)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc


@router.post(
    "/{campaign_id}/messages",
    response_model=CampaignMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def create_message(
    campaign_id: uuid.UUID,
    payload: CampaignMessageCreate,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignMessageRead:
    try:
        message = service.create_message(campaign_id, payload)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc
    return CampaignMessageRead.model_validate(message)


@router.patch("/{campaign_id}/messages/{message_id}", response_model=CampaignMessageRead)
def update_message(
    campaign_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: CampaignMessageUpdate,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignMessageRead:
    try:
        message = service.update_message(campaign_id, message_id, payload)
    except (CampaignNotFoundError, CampaignMessageNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc
    return CampaignMessageRead.model_validate(message)


@router.delete(
    "/{campaign_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_message(
    campaign_id: uuid.UUID,
    message_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
) -> None:
    try:
        service.delete_message(campaign_id, message_id)
    except (CampaignNotFoundError, CampaignMessageNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{campaign_id}/prospects",
    response_model=CampaignProspectRead,
    status_code=status.HTTP_201_CREATED,
)
def attach_prospect(
    campaign_id: uuid.UUID,
    payload: CampaignProspectAttach,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignProspectRead:
    try:
        link = service.attach_prospect(campaign_id, payload.prospect_id)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc
    return CampaignProspectRead(
        id=link.id,
        campaign_id=link.campaign_id,
        prospect_id=link.prospect_id,
        created_at=link.created_at,
        prospect=ProspectRead.model_validate(link.prospect) if link.prospect else None,
    )


@router.delete(
    "/{campaign_id}/prospects/{prospect_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def detach_prospect(
    campaign_id: uuid.UUID,
    prospect_id: uuid.UUID,
    service: CampaignService = Depends(get_campaign_service),
) -> None:
    try:
        service.detach_prospect(campaign_id, prospect_id)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CampaignValidationError as exc:
        raise _http_from_validation(exc) from exc


@router.get("/{campaign_id}/instantly/preview", response_model=InstantlyCampaignPreviewResponse)
def preview_instantly_campaign(
    campaign_id: uuid.UUID,
    sync_service: InstantlyCampaignSyncService = Depends(get_sync_service),
) -> InstantlyCampaignPreviewResponse:
    try:
        preview = sync_service.preview(campaign_id)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InstantlySyncError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    return InstantlyCampaignPreviewResponse(
        name=preview.name,
        schedule_label=preview.schedule_label,
        timezone=preview.timezone,
        window_start=preview.window_start,
        window_end=preview.window_end,
        days_label=preview.days_label,
        steps=[
            InstantlyPreviewStep(
                order=step.order,
                label=step.label,
                subject=step.subject,
                delay_label=step.delay_label,
                delay_hours=step.delay_hours,
            )
            for step in preview.steps
        ],
    )


@router.post("/{campaign_id}/instantly/sync", response_model=InstantlySyncResponse)
def sync_instantly_campaign(
    campaign_id: uuid.UUID,
    sync_service: InstantlyCampaignSyncService = Depends(get_sync_service),
) -> InstantlySyncResponse:
    try:
        result = sync_service.sync(campaign_id)
    except CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InstantlySyncError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    return InstantlySyncResponse(
        campaign_id=result.campaign_id,
        instantly_campaign_id=result.instantly_campaign_id,
        sync_status=result.sync_status,
        last_synced_at=result.last_synced_at,
        sync_error=result.sync_error,
        created=result.created,
        message=result.message,
    )
