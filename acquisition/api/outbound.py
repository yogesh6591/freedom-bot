"""Outbound API routes: assessment, settings, replies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.channels import list_channel_statuses
from acquisition.db.session import get_db
from acquisition.models.campaign import CampaignProspect
from acquisition.models.replies import ReplyConversation
from acquisition.models.settings import AIReplySettings
from acquisition.services.assessment_service import AssessmentEventError, AssessmentEventService
from acquisition.auth.admin import require_admin_api

admin_router = APIRouter(tags=["outbound"], dependencies=[Depends(require_admin_api)])
router = admin_router  # backward-compatible alias


def get_assessment(db: Session = Depends(get_db)) -> AssessmentEventService:
    return AssessmentEventService(db)


class AIReplySettingsUpdate(BaseModel):
    auto_reply_enabled: bool | None = None
    system_prompt: str | None = None
    assessment_system_prompt: str | None = None
    qualification_system_prompt: str | None = None


class AIReplySettingsRead(BaseModel):
    auto_reply_enabled: bool
    system_prompt: str
    assessment_system_prompt: str
    qualification_system_prompt: str
    provider_configured: bool
    protected_rules_note: str = (
        "Core safety rules are enforced in code and are not editable here."
    )


class AssessmentStartedRequest(BaseModel):
    token: str = Field(..., min_length=8)


@router.post("/api/assessment/started")
def assessment_started(
    payload: AssessmentStartedRequest,
    service: AssessmentEventService = Depends(get_assessment),
) -> dict[str, Any]:
    try:
        link = service.assessment_started(payload.token)
    except AssessmentEventError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    return {
        "ok": True,
        "campaign_prospect_id": str(link.id),
        "assessment_status": link.assessment_status.value,
        "outreach_status": link.outreach_status.value,
    }


@router.get("/api/settings/ai-replies", response_model=AIReplySettingsRead)
def get_ai_settings(db: Session = Depends(get_db)) -> AIReplySettingsRead:
    from acquisition.models.settings import (
        DEFAULT_AI_SYSTEM_PROMPT,
        DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
        DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    )
    from acquisition.services.openai_service import OpenAIService

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
    return AIReplySettingsRead(
        auto_reply_enabled=row.auto_reply_enabled,
        system_prompt=row.system_prompt,
        assessment_system_prompt=row.assessment_system_prompt,
        qualification_system_prompt=row.qualification_system_prompt,
        provider_configured=provider_ok,
    )


@router.patch("/api/settings/ai-replies", response_model=AIReplySettingsRead)
def update_ai_settings(
    payload: AIReplySettingsUpdate,
    db: Session = Depends(get_db),
) -> AIReplySettingsRead:
    from acquisition.models.settings import (
        DEFAULT_AI_SYSTEM_PROMPT,
        DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
        DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    )

    row = db.get(AIReplySettings, 1)
    if row is None:
        row = AIReplySettings(
            id=1,
            auto_reply_enabled=False,
            system_prompt=DEFAULT_AI_SYSTEM_PROMPT,
            assessment_system_prompt=DEFAULT_ASSESSMENT_SYSTEM_PROMPT,
            qualification_system_prompt=DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
        )
    if payload.auto_reply_enabled is not None:
        row.auto_reply_enabled = payload.auto_reply_enabled
    if payload.system_prompt is not None:
        row.system_prompt = payload.system_prompt
    if payload.assessment_system_prompt is not None:
        row.assessment_system_prompt = (
            payload.assessment_system_prompt.strip() or DEFAULT_ASSESSMENT_SYSTEM_PROMPT
        )
    if payload.qualification_system_prompt is not None:
        row.qualification_system_prompt = (
            payload.qualification_system_prompt.strip()
            or DEFAULT_QUALIFICATION_SYSTEM_PROMPT
        )
    db.add(row)
    db.commit()
    db.refresh(row)
    return get_ai_settings(db)


@router.get("/api/channels")
def channels() -> dict[str, Any]:
    return {
        "items": [
            {
                "channel": s.channel.value,
                "configured": s.configured,
                "provider_name": s.provider_name,
                "message": s.message,
            }
            for s in list_channel_statuses()
        ]
    }


@router.get("/api/replies")
def list_replies(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(ReplyConversation)
            .options(selectinload(ReplyConversation.messages))
            .order_by(ReplyConversation.updated_at.desc())
        ).all()
    )
    items = []
    for conv in rows:
        link = db.get(CampaignProspect, conv.campaign_prospect_id)
        prospect = link.prospect if link else None
        campaign = link.campaign if link else None
        items.append(
            {
                "id": str(conv.id),
                "campaign_prospect_id": str(conv.campaign_prospect_id),
                "status": conv.status.value,
                "latest_incoming_snippet": conv.latest_incoming_snippet,
                "prospect": prospect.executive_name if prospect else None,
                "company": prospect.company if prospect else None,
                "campaign": campaign.name if campaign else None,
                "assessment_status": link.assessment_status.value if link else None,
                "outreach_status": link.outreach_status.value if link else None,
                "last_activity_at": link.last_activity_at.isoformat() if link and link.last_activity_at else None,
                "message_count": len(conv.messages),
            }
        )
    return {"items": items}
