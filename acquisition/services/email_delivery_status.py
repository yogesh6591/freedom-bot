"""Honest Instantly email delivery state for admin UI.

Syncing a lead to Instantly is not the same as Instantly sending mail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.campaign import CampaignProspect, LeadSyncStatus, OutreachStatus
from acquisition.models.replies import ProviderEvent

# Statuses that imply Instantly has already contacted the lead (or tried).
_CONTACTED_OUTREACH = frozenset(
    {
        OutreachStatus.REPLIED,
        OutreachStatus.ASSESSMENT_STARTED,
        OutreachStatus.COMPLETED,
        OutreachStatus.BOUNCED,
        OutreachStatus.UNSUBSCRIBED,
    }
)


def first_email_sent_at(db: Session, link_id: Any) -> datetime | None:
    event = db.scalar(
        select(ProviderEvent)
        .where(
            ProviderEvent.provider == "instantly",
            ProviderEvent.event_type == "email_sent",
            ProviderEvent.campaign_prospect_id == link_id,
        )
        .order_by(ProviderEvent.created_at.asc())
        .limit(1)
    )
    if event is None:
        return None
    return event.processed_at or event.created_at


def resolve_email_delivery(db: Session, link: CampaignProspect) -> dict[str, Any]:
    """Return status: sent | queued | not_sent plus labels for the status strip."""
    sent_at = first_email_sent_at(db, link.id)
    outreach = link.outreach_status
    if sent_at is not None or (outreach is not None and outreach in _CONTACTED_OUTREACH):
        return {
            "status": "sent",
            "label": "Sent",
            "done": True,
            "sent_at": sent_at,
        }

    synced = bool(link.instantly_lead_id) or link.sync_status == LeadSyncStatus.SYNCED
    if synced:
        return {
            "status": "queued",
            "label": "Queued in Instantly",
            "done": False,
            "sent_at": None,
        }

    return {
        "status": "not_sent",
        "label": "Not sent",
        "done": False,
        "sent_at": None,
    }
