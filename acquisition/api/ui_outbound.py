"""Admin UI for replies, AI settings, and campaign outbound actions."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.db.session import get_db
from acquisition.models.replies import ReplyConversation
from acquisition.models.settings import AIReplySettings
from acquisition.templating import create_templates
from acquisition.auth.admin import require_admin_html_request

templates = create_templates()

router = APIRouter(tags=["admin-ui-outbound"], dependencies=[Depends(require_admin_html_request)])

_ENGINE_RETIRED = "The old discovery engine is retired. Use Prospecting rules."


def _retired_engine(request: Request) -> JSONResponse | RedirectResponse:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept or request.query_params.get("format") == "json":
        return JSONResponse({"error": _ENGINE_RETIRED}, status_code=410)
    return RedirectResponse(
        url="/settings/ai?flash=engine_retired",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/replies", response_model=None)
def replies_page() -> RedirectResponse:
    """Legacy inbox URL — replies live on Assessment detail now."""
    return RedirectResponse(
        url="/assessments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/replies/{conversation_id}", response_model=None)
def reply_detail(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Legacy thread URL — open the assessment Email replies tab."""
    conv = db.get(ReplyConversation, conversation_id)
    if conv is None:
        return RedirectResponse(
            url="/assessments?flash=not_found",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/assessments/{conv.campaign_prospect_id}?tab=replies",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/settings/ai", response_class=HTMLResponse)
def ai_settings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    from acquisition.models.settings import (
        DEFAULT_AI_SYSTEM_PROMPT,
        DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
        DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    )
    from acquisition.services.apollo_client import apollo_key_configured
    from acquisition.services.pdl_client import pdl_key_configured
    from acquisition.services.openai_service import OpenAIService
    from acquisition.services.prospecting_rules_service import get_prospecting_rules

    row = db.get(AIReplySettings, 1)
    if row is None:
        row = AIReplySettings(
            id=1,
            auto_reply_enabled=False,
            system_prompt=DEFAULT_AI_SYSTEM_PROMPT,
            assessment_system_prompt=DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
            qualification_system_prompt=DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    provider_ok = OpenAIService(db=db).get_summary().configured
    return templates.TemplateResponse(
        request,
        "settings/ai.html",
        {
            "settings": row,
            "provider_configured": provider_ok,
            "prospecting": get_prospecting_rules().to_dict(),
            "apollo_configured": apollo_key_configured(),
            "pdl_configured": pdl_key_configured(),
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/settings/ai", response_model=None)
def ai_settings_save(
    auto_reply_enabled: Annotated[str | None, Form()] = None,
    system_prompt: Annotated[str, Form()] = "",
    assessment_system_prompt: Annotated[str, Form()] = "",
    qualification_system_prompt: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    from acquisition.models.settings import (
        DEFAULT_AI_SYSTEM_PROMPT,
        DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
        DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    )

    row = db.get(AIReplySettings, 1)
    if row is None:
        row = AIReplySettings(
            id=1,
            system_prompt=DEFAULT_AI_SYSTEM_PROMPT,
            assessment_system_prompt=DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
            qualification_system_prompt=DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
        )
    row.auto_reply_enabled = auto_reply_enabled == "true"
    row.system_prompt = system_prompt.strip() or DEFAULT_AI_SYSTEM_PROMPT
    row.assessment_system_prompt = (
        assessment_system_prompt.strip() or DEFAULT_ASSESSMENT_SYSTEM_PROMPT
    )
    row.qualification_system_prompt = (
        qualification_system_prompt.strip() or DEFAULT_QUALIFICATION_SYSTEM_PROMPT
    )
    db.add(row)
    db.commit()
    return RedirectResponse(url="/settings/ai?flash=saved", status_code=status.HTTP_303_SEE_OTHER)



@router.post("/settings/ai/icp", response_model=None)
@router.post("/settings/ai/discovery-cycle", response_model=None)
@router.post("/settings/ai/activation-config", response_model=None)
@router.post("/settings/ai/activation-preview", response_model=None)
@router.post("/settings/ai/activation-batch", response_model=None)
@router.post("/settings/ai/automation-config", response_model=None)
@router.get("/settings/ai/automation/discovery-status", response_model=None)
@router.get("/settings/ai/automation/discovery-preflight", response_model=None)
@router.post("/settings/ai/automation/discovery-now", response_model=None)
@router.post("/settings/ai/automation/activation-dry-run", response_model=None)
@router.post("/settings/ai/automation/activation-now", response_model=None)
@router.post("/settings/ai/live-e2e", response_model=None)
def retired_old_engine(request: Request):
    """Old ICP discovery/outreach engine — routes kept so bookmarks 410 instead of 404."""
    return _retired_engine(request)





