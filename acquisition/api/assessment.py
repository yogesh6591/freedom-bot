"""Public Conversational CAIO Assessment UI and JSON APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from acquisition.db.session import get_db
from acquisition.services.assessment_chat_service import AssessmentChatError, AssessmentChatService
from acquisition.services.assessment_service import AssessmentEventError
from acquisition.templating import create_templates

templates = create_templates()
router = APIRouter(tags=["assessment"])

BRAND = "JeanneCAIO / FreedomBot"


def get_assessment_chat(db: Session = Depends(get_db)) -> AssessmentChatService:
    return AssessmentChatService(db)


class AssessmentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@router.get("/a/{token}", response_class=HTMLResponse)
def assessment_chat_page(
    request: Request,
    token: str,
    service: AssessmentChatService = Depends(get_assessment_chat),
) -> HTMLResponse:
    try:
        session = service.get_session(token)
    except AssessmentEventError as exc:
        return templates.TemplateResponse(
            request,
            "assessment/invalid.html",
            {"message": exc.message},
            status_code=exc.http_status if exc.http_status in {404, 422} else 404,
        )
    if session.get("completed"):
        return RedirectResponse(url=f"/a/{token}/result", status_code=302)
    return templates.TemplateResponse(
        request,
        "assessment/chat.html",
        {
            "token": token,
            "session": session,
            "brand": BRAND,
        },
    )


@router.get("/a/{token}/result", response_class=HTMLResponse)
def assessment_result_page(
    request: Request,
    token: str,
    service: AssessmentChatService = Depends(get_assessment_chat),
) -> HTMLResponse:
    try:
        payload = service.get_result(token)
    except AssessmentEventError as exc:
        return templates.TemplateResponse(
            request,
            "assessment/invalid.html",
            {"message": exc.message},
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "assessment/result.html",
        {
            "token": token,
            "payload": payload,
            "brand": BRAND,
        },
    )


@router.get("/api/assessment/{token}/session")
def assessment_session(
    token: str,
    service: AssessmentChatService = Depends(get_assessment_chat),
) -> dict[str, Any]:
    try:
        return service.get_session(token)
    except AssessmentEventError as exc:
        raise _http_error(exc) from exc


@router.post("/api/assessment/{token}/greeting")
def assessment_greeting(
    token: str,
    service: AssessmentChatService = Depends(get_assessment_chat),
) -> dict[str, Any]:
    try:
        return service.ensure_greeting(token)
    except AssessmentEventError as exc:
        raise _http_error(exc) from exc
    except AssessmentChatError as exc:
        raise _http_error(exc) from exc


@router.post("/api/assessment/{token}/messages", response_model=None)
def assessment_send_message(
    token: str,
    payload: AssessmentMessageRequest,
    service: AssessmentChatService = Depends(get_assessment_chat),
):
    try:
        return service.send_message(token, payload.content)
    except AssessmentEventError as exc:
        raise _http_error(exc) from exc
    except AssessmentChatError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"ok": False, "error": exc.message, "code": exc.code},
        )


@router.get("/api/assessment/{token}/result")
def assessment_result_api(
    token: str,
    service: AssessmentChatService = Depends(get_assessment_chat),
) -> dict[str, Any]:
    try:
        data = service.get_result(token)
    except AssessmentEventError as exc:
        raise _http_error(exc) from exc
    return {
        "token": data["token"],
        "assessment_status": data["assessment_status"],
        "journey_stage": data.get("journey_stage"),
        "completed": data["completed"],
        "completed_at": data["completed_at"],
        "result": data["result"],
    }


def _http_error(exc: AssessmentEventError | AssessmentChatError):
    from fastapi import HTTPException

    status = getattr(exc, "http_status", 400)
    detail = getattr(exc, "message", str(exc))
    return HTTPException(status_code=status, detail=detail)
