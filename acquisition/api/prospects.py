"""Prospect REST API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_api
from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.models.prospect import ApprovalStatus
from acquisition.schemas.prospect import (
    ProspectCreate,
    ProspectListResponse,
    ProspectRead,
    ProspectUpdate,
)
from acquisition.services.prospect_service import ProspectNotFoundError, ProspectService

router = APIRouter(
    prefix="/api/prospects",
    tags=["prospects"],
    dependencies=[Depends(require_admin_api)],
)


def get_prospect_service(db: Session = Depends(get_db)) -> ProspectService:
    return ProspectService(db)


@router.get("", response_model=ProspectListResponse)
def list_prospects(
    search: str | None = Query(default=None),
    approval_status: ApprovalStatus | None = Query(default=None),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    service: ProspectService = Depends(get_prospect_service),
) -> ProspectListResponse:
    settings = get_settings()
    size = page_size or settings.default_page_size
    size = min(size, settings.max_page_size)
    items, total = service.list_prospects(
        search=search,
        approval_status=approval_status,
        source=source,
        page=page,
        page_size=size,
    )
    return ProspectListResponse(
        items=[ProspectRead.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=size,
    )


@router.get("/{prospect_id}", response_model=ProspectRead)
def get_prospect(
    prospect_id: uuid.UUID,
    service: ProspectService = Depends(get_prospect_service),
) -> ProspectRead:
    try:
        prospect = service.get_prospect(prospect_id)
    except ProspectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProspectRead.model_validate(prospect)


@router.post("", response_model=ProspectRead, status_code=status.HTTP_201_CREATED)
def create_prospect(
    payload: ProspectCreate,
    service: ProspectService = Depends(get_prospect_service),
) -> ProspectRead:
    prospect = service.create_prospect(payload)
    return ProspectRead.model_validate(prospect)


@router.patch("/{prospect_id}", response_model=ProspectRead)
def update_prospect(
    prospect_id: uuid.UUID,
    payload: ProspectUpdate,
    service: ProspectService = Depends(get_prospect_service),
) -> ProspectRead:
    try:
        prospect = service.update_prospect(prospect_id, payload)
    except ProspectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProspectRead.model_validate(prospect)


@router.delete("/{prospect_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_prospect(
    prospect_id: uuid.UUID,
    service: ProspectService = Depends(get_prospect_service),
) -> None:
    try:
        service.delete_prospect(prospect_id)
    except ProspectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
