"""Cal.com webhook ingress for booking confirmation."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from acquisition.db.session import get_db
from acquisition.services.booking_service import BookingAuthError, BookingService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["calcom-webhooks"])


def get_booking(db: Session = Depends(get_db)) -> BookingService:
    return BookingService(db)


@router.post("/api/webhooks/calcom")
async def calcom_webhook(
    request: Request,
    booking: BookingService = Depends(get_booking),
    x_cal_signature_256: str | None = Header(default=None, alias="x-cal-signature-256"),
) -> dict[str, Any]:
    raw = await request.body()
    try:
        booking.verify_calcom_signature(raw_body=raw, signature=x_cal_signature_256)
    except BookingAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    # Never log secrets / full payloads with PII beyond what's needed.
    trigger = str(payload.get("triggerEvent") or "")
    event_id = _event_id(payload, raw)
    logger.info("Cal.com webhook received trigger=%s event_id=%s", trigger, event_id[:24])
    result = booking.handle_calcom_webhook(payload, event_id=event_id)
    return result


def _event_id(payload: dict[str, Any], raw: bytes) -> str:
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    uid = ""
    if isinstance(body, dict):
        uid = str(body.get("uid") or body.get("bookingUid") or body.get("id") or "")
    trigger = str(payload.get("triggerEvent") or payload.get("trigger") or "event")
    created = str(payload.get("createdAt") or "")
    base = f"{trigger}:{uid}:{created}"
    if uid or created:
        return base[:240]
    return hashlib.sha256(raw).hexdigest()
