"""Email delivery state for the admin pages, read from our own sent-email log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.campaign import CampaignProspect, OutreachStatus
from acquisition.models.outreach import STATUS_QUEUED, STATUS_SENDING, STATUS_SENT, SentEmail

# Journey states that only happen after we emailed the person.
_CONTACTED_OUTREACH = frozenset(
    {
        OutreachStatus.REPLIED,
        OutreachStatus.ASSESSMENT_STARTED,
        OutreachStatus.COMPLETED,
        OutreachStatus.BOUNCED,
        OutreachStatus.UNSUBSCRIBED,
    }
)


def first_email_sent_at(db: Session, prospect_id: Any) -> datetime | None:
    return db.scalar(
        select(SentEmail.sent_at)
        .where(SentEmail.prospect_id == prospect_id, SentEmail.status == STATUS_SENT)
        .order_by(SentEmail.sent_at.asc())
        .limit(1)
    )


def resolve_email_delivery(db: Session, link: CampaignProspect) -> dict[str, Any]:
    """Return status: sent | queued | not_sent plus labels for the status strip."""
    sent_at = first_email_sent_at(db, link.prospect_id)
    outreach = link.outreach_status
    if sent_at is not None or (outreach is not None and outreach in _CONTACTED_OUTREACH):
        return {"status": "sent", "label": "Sent", "done": True, "sent_at": sent_at}

    waiting = db.scalar(
        select(SentEmail.id)
        .where(
            SentEmail.prospect_id == link.prospect_id,
            SentEmail.status.in_([STATUS_QUEUED, STATUS_SENDING]),
        )
        .limit(1)
    )
    if waiting is not None:
        return {"status": "queued", "label": "Waiting to be sent", "done": False, "sent_at": None}

    return {"status": "not_sent", "label": "Not sent", "done": False, "sent_at": None}
