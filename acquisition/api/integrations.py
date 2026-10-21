"""Instantly integration API routes (never return decrypted credentials)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from acquisition.db.session import get_db
from acquisition.schemas.integration import (
    InstantlyConfigCreate,
    InstantlyConfigSummary,
    InstantlyConfigUpdate,
    InstantlyStatusPayload,
)
from acquisition.services.instantly_service import (
    InstantlyConfigErrorApp,
    InstantlyService,
    InstantlyStatusResponse,
)
from acquisition.auth.admin import require_admin_api

router = APIRouter(
    prefix="/api/integrations/instantly",
    tags=["integrations"],
    dependencies=[Depends(require_admin_api)],
)


def get_instantly_service(db: Session = Depends(get_db)) -> InstantlyService:
    return InstantlyService(db=db)


@router.get("", response_model=InstantlyConfigSummary | None)
def get_instantly_config(
    service: InstantlyService = Depends(get_instantly_service),
) -> InstantlyConfigSummary | None:
    return service.get_summary()


@router.post("", response_model=InstantlyConfigSummary, status_code=status.HTTP_201_CREATED)
def create_instantly_config(
    payload: InstantlyConfigCreate,
    service: InstantlyService = Depends(get_instantly_service),
) -> InstantlyConfigSummary:
    try:
        return service.create_connection(payload)
    except InstantlyConfigErrorApp as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message) from exc


@router.patch("", response_model=InstantlyConfigSummary)
def update_instantly_config(
    payload: InstantlyConfigUpdate,
    service: InstantlyService = Depends(get_instantly_service),
) -> InstantlyConfigSummary:
    try:
        return service.update_connection(payload)
    except InstantlyConfigErrorApp as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message) from exc


@router.post("/test", response_model=InstantlyStatusPayload)
def test_instantly_connection(
    service: InstantlyService = Depends(get_instantly_service),
) -> InstantlyStatusPayload | JSONResponse:
    return _status_response(service.check_connection())


@router.get("/status", response_model=InstantlyStatusPayload)
def instantly_status(
    service: InstantlyService = Depends(get_instantly_service),
) -> InstantlyStatusPayload | JSONResponse:
    return _status_response(service.check_connection())


def _status_response(result: InstantlyStatusResponse) -> InstantlyStatusPayload | JSONResponse:
    payload = InstantlyStatusPayload(
        connected=result.connected,
        message=result.message,
        display_name=result.display_name,
        connection_status=result.connection_status,
        api_key_configured=result.api_key_configured,
        last_checked_at=result.last_checked_at,
    )
    if result.http_status == 200:
        return payload
    return JSONResponse(status_code=result.http_status, content=payload.model_dump(mode="json"))
