"""Assessment lifecycle events: started / completed ."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.models.campaign import (
    AssessmentStatus,
    CampaignProspect,
    JourneyStage,
    OutreachStatus,
    ReplyAIStatus,
)
from acquisition.models.replies import ReplyConversation


class AssessmentEventError(Exception):
    def __init__(self, message: str, *, http_status: int = 404) -> None:
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class AssessmentEventService:
    """Lifecycle hooks for assessment progress on a campaign prospect link."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_link(self, token: str) -> CampaignProspect:
        cleaned = (token or "").strip()
        if not cleaned:
            raise AssessmentEventError("Assessment token is required", http_status=422)
        link = self.db.scalar(
            select(CampaignProspect)
            .where(CampaignProspect.assessment_token == cleaned)
            .options(
                selectinload(CampaignProspect.prospect),
                selectinload(CampaignProspect.campaign),
            )
        )
        if link is None:
            raise AssessmentEventError("Unknown assessment token", http_status=404)
        return link

    def assessment_started(self, token: str) -> CampaignProspect:
        """Mark started on first real user interaction. Idempotent if already started/completed."""
        link = self.resolve_link(token)
        if link.assessment_status in {AssessmentStatus.STARTED, AssessmentStatus.COMPLETED}:
            return link

        now = datetime.now(timezone.utc)
        link.assessment_status = AssessmentStatus.STARTED
        link.outreach_status = OutreachStatus.ASSESSMENT_STARTED
        link.assessment_started_at = now
        link.last_activity_at = now
        if link.journey_stage == JourneyStage.OUTREACH:
            link.journey_stage = JourneyStage.ASSESSMENT_STARTED
        self.db.add(link)

        conversation = self.db.scalar(
            select(ReplyConversation).where(ReplyConversation.campaign_prospect_id == link.id)
        )
        if conversation:
            conversation.status = ReplyAIStatus.ASSESSMENT_STARTED
            self.db.add(conversation)

        self.db.commit()
        self.db.refresh(link)

        return link

    def assessment_completed(self, token: str) -> CampaignProspect:
        """Mark assessment completed. Idempotent if already completed."""
        link = self.resolve_link(token)
        if link.assessment_status == AssessmentStatus.COMPLETED:
            return link

        now = datetime.now(timezone.utc)
        if link.assessment_status == AssessmentStatus.NOT_STARTED:
            link.assessment_started_at = link.assessment_started_at or now
        link.assessment_status = AssessmentStatus.COMPLETED
        link.assessment_completed_at = now
        link.last_activity_at = now
        if link.journey_stage in {JourneyStage.OUTREACH, JourneyStage.ASSESSMENT_STARTED}:
            link.journey_stage = JourneyStage.ASSESSMENT_COMPLETED
        if link.outreach_status not in {
            OutreachStatus.STOPPED,
            OutreachStatus.UNSUBSCRIBED,
            OutreachStatus.BOUNCED,
        }:
            link.outreach_status = OutreachStatus.ASSESSMENT_STARTED
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return link
