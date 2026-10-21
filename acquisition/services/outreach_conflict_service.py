"""Block conflicting ACTIVE Instantly outreach for the same person."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from acquisition.models.campaign import (
    AssessmentStatus,
    Campaign,
    CampaignProspect,
    CampaignStatus,
    JourneyStage,
    LeadSyncStatus,
    OutreachStatus,
)
from acquisition.models.prospect import Prospect

# Outreach statuses that mean Instantly/email is no longer actively contacting.
_TERMINAL_OUTREACH = frozenset(
    {
        OutreachStatus.STOPPED,
        OutreachStatus.UNSUBSCRIBED,
        OutreachStatus.BOUNCED,
        OutreachStatus.COMPLETED,
        OutreachStatus.COLD,
    }
)


@dataclass(frozen=True, slots=True)
class ActiveOutreachConflict:
    blocking_link_id: object
    blocking_campaign_id: object
    blocking_campaign_name: str
    reason: str

    @property
    def admin_message(self) -> str:
        return f"Prospect already has active outreach in {self.blocking_campaign_name}."


def normalize_outreach_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_active_outreach_link(link: CampaignProspect, *, campaign: Campaign | None = None) -> bool:
    """True when this journey hub is (or will be) actively contacting the person."""
    camp = campaign or getattr(link, "campaign", None)
    if camp is not None and camp.status == CampaignStatus.ARCHIVED:
        return False

    outreach = link.outreach_status
    if outreach in _TERMINAL_OUTREACH:
        return False

    if outreach in {
        OutreachStatus.ACTIVE,
        OutreachStatus.REPLIED,
        OutreachStatus.ASSESSMENT_STARTED,
    }:
        return True

    if link.instantly_lead_id and link.sync_status == LeadSyncStatus.SYNCED:
        return True

    if link.assessment_started_at is not None:
        return True
    if link.assessment_status != AssessmentStatus.NOT_STARTED:
        return True
    if link.booked_at is not None:
        return True
    if (link.booking_status or "").strip().lower() == "confirmed":
        return True
    if link.journey_stage not in {JourneyStage.OUTREACH}:
        return True

    return False


def find_active_outreach_conflict(
    db: Session,
    *,
    prospect_id: object,
    email: str | None,
    excluding_link_id: object | None = None,
    excluding_campaign_id: object | None = None,
) -> ActiveOutreachConflict | None:
    """Return the first conflicting active CampaignProspect for this person.

    Matches by prospect_id OR normalized email so duplicate Prospect rows cannot
    bypass the guard. Historical/inactive hubs do not block.
    """
    normalized = normalize_outreach_email(email)
    stmt = (
        select(CampaignProspect)
        .options(
            selectinload(CampaignProspect.campaign),
            selectinload(CampaignProspect.prospect),
        )
        .join(Prospect, Prospect.id == CampaignProspect.prospect_id)
    )
    identity_filters = [CampaignProspect.prospect_id == prospect_id]
    if normalized:
        identity_filters.append(func.lower(Prospect.email) == normalized)
    stmt = stmt.where(or_(*identity_filters))

    for link in db.scalars(stmt).all():
        if excluding_link_id is not None and link.id == excluding_link_id:
            continue
        if excluding_campaign_id is not None and link.campaign_id == excluding_campaign_id:
            continue
        campaign = link.campaign
        if not is_active_outreach_link(link, campaign=campaign):
            continue
        name = (campaign.name if campaign else None) or "another campaign"
        return ActiveOutreachConflict(
            blocking_link_id=link.id,
            blocking_campaign_id=link.campaign_id,
            blocking_campaign_name=name,
            reason="active_outreach",
        )
    return None
