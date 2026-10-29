"""Database reads for outreach: who the people are, what we already sent them, where they are in the journey."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from acquisition.models.assessment import AssessmentConversation, AssessmentProfile
from acquisition.models.campaign import AssessmentStatus, CampaignProspect, OutreachStatus
from acquisition.models.outreach import STATUS_QUEUED, STATUS_SENT
from acquisition.models.prospect import Prospect
from acquisition.services.assessment_tokens import build_assessment_url, split_executive_name
from acquisition.services import outreach_service as svc

OPTED_OUT = (OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED)


def link_row(db: Session, prospect_id: uuid.UUID) -> Optional[CampaignProspect]:
    camp = db.scalar(select(svc.Campaign).where(svc.Campaign.name == svc.DIRECT_CAMPAIGN_NAME))
    if camp is not None:
        row = db.scalar(
            select(CampaignProspect).where(
                CampaignProspect.campaign_id == camp.id, CampaignProspect.prospect_id == prospect_id
            )
        )
        if row is not None:
            return row
    # People imported from the old campaign days: use their most advanced existing link.
    return db.scalar(
        select(CampaignProspect)
        .where(CampaignProspect.prospect_id == prospect_id)
        .order_by(CampaignProspect.created_at.desc())
        .limit(1)
    )


def people_records(db: Session, emails: list[str] | None = None) -> dict:
    """The ``records`` dict the card helpers read: one entry per person with name, company and link."""
    q = select(Prospect)
    if emails is not None:
        keys = [e.strip().lower() for e in emails if e and e.strip()]
        if not keys:
            return {"people": [], "assessment_url": ""}
        q = q.where(func.lower(Prospect.email).in_(keys))
    people = []
    for p in db.scalars(q.order_by(Prospect.executive_name)):
        link = link_row(db, p.id)
        first, _last = split_executive_name(p.executive_name)
        people.append(
            {
                "id": str(p.id),
                "name": p.executive_name,
                "first_name": first,
                "email": p.email,
                "company": (p.company or "").strip(),
                "assessment_url": build_assessment_url(link.assessment_token)
                if link is not None and link.assessment_token
                else "",
            }
        )
    from acquisition.config import get_settings

    return {"people": people, "assessment_url": (get_settings().public_base_url or "").strip()}


def find_prospects(db: Session, query: str, limit: int = 8) -> list[Prospect]:
    """Match an email, a full name, a first name or a company (case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    exact = list(db.scalars(select(Prospect).where(func.lower(Prospect.email) == q)))
    if exact:
        return exact
    like = f"%{q}%"
    return list(
        db.scalars(
            select(Prospect)
            .where(
                or_(
                    func.lower(Prospect.executive_name).like(like),
                    func.lower(Prospect.company).like(like),
                    func.lower(Prospect.email).like(like),
                )
            )
            .order_by(Prospect.executive_name)
            .limit(limit)
        )
    )


def email_state(db: Session, prospect_id: uuid.UUID) -> dict:
    """What we know about emails to one person: count, last email, opt-out."""
    rows = svc.history_for(db, prospect_id)
    live = [r for r in rows if r.status in (STATUS_SENT, STATUS_QUEUED)]
    last = live[-1] if live else None
    link = link_row(db, prospect_id)
    return {
        "emails": len(live),
        "sent": sum(1 for r in rows if r.status == STATUS_SENT),
        "queued": sum(1 for r in rows if r.status == STATUS_QUEUED),
        "last_subject": last.subject if last else "",
        "last_status": last.status if last else "",
        "last_at": (last.sent_at or last.queued_at) if last else None,
        "opted_out": bool(link is not None and link.outreach_status in OPTED_OUT),
        "opt_status": link.outreach_status.value if link is not None else "",
    }


def assessment_stage(db: Session, prospect_id: uuid.UUID) -> dict:
    """Where the person is in the assessment journey. Page-open is not email-click tracking."""
    link = link_row(db, prospect_id)
    if link is None or not link.assessment_token:
        return {"has_link": False}
    conv = db.scalar(
        select(AssessmentConversation).where(AssessmentConversation.campaign_prospect_id == link.id)
    )
    profile = db.scalar(select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id))
    out = {
        "has_link": True,
        "link": build_assessment_url(link.assessment_token),
        "page_opened_at": conv.created_at if conv is not None else None,
        "assessment_status": link.assessment_status.value,
        "started_at": link.assessment_started_at,
        "completed_at": link.assessment_completed_at,
        "journey_stage": link.journey_stage.value,
        "booking_status": link.booking_status,
        "booked_at": link.booked_at,
        "meeting_start": link.meeting_start,
    }
    if profile is not None:
        out["qualification_status"] = getattr(profile.qualification_status, "value", str(profile.qualification_status))
        out["qualification"] = profile.qualification or {}
        out["result"] = profile.result_payload or {}
        out["structured_state"] = profile.structured_state or {}
        out["pre_call_brief_status"] = profile.pre_call_brief_status
        out["pre_call_brief"] = profile.pre_call_brief_data or {}
    return out


def display_status(db: Session, prospect_id: uuid.UUID) -> str:
    """One honest word for the list view. A finished assessment beats 'started the assessment'."""
    st = email_state(db, prospect_id)
    if st["opted_out"]:
        return st["opt_status"]
    stage = assessment_stage(db, prospect_id)
    if stage.get("has_link"):
        if stage["booking_status"] not in ("not_booked", "", None):
            return "booked"
        if stage["assessment_status"] == AssessmentStatus.COMPLETED.value:
            return "finished the assessment"
        if stage["assessment_status"] == AssessmentStatus.STARTED.value:
            return "started the assessment"
        if stage["page_opened_at"] is not None:
            return "opened the assessment page"
    if st["queued"] and not st["sent"]:
        return "email queued"
    if st["sent"]:
        return "emailed"
    return "not emailed yet"


def ensure_prospect(db: Session, email: str, name: str = "", company: str = "") -> Prospect:
    """The prospect row for an address; creates a minimal one when the user typed a new address."""
    found = db.scalar(select(Prospect).where(func.lower(Prospect.email) == email.strip().lower()))
    if found is not None:
        stated = (name or "").strip()
        local = found.email.split("@", 1)[0].strip().casefold()
        if stated and (found.executive_name or "").strip().casefold() in {local, found.email.casefold(), ""}:
            found.executive_name = stated  # replace a mailbox stored as a name with the name the user gave
        return found
    local = email.split("@", 1)[0]
    row = Prospect(
        company=(company or "").strip(),
        executive_name=(name or "").strip() or local,
        email=email.strip(),
        discovery_source="assistant",
    )
    db.add(row)
    db.flush()
    return row


def replace_person_name(*, email: str, first_name: str, last_name: str, company: str) -> None:
    """Card 'save for every later email': change the stored name and company."""
    from acquisition.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.scalar(select(Prospect).where(func.lower(Prospect.email) == email.strip().lower()))
        if row is None:
            return
        full = " ".join(x for x in ((first_name or "").strip(), (last_name or "").strip()) if x)
        if full:
            row.executive_name = full
        if company is not None and company.strip():
            row.company = company.strip()
        db.commit()
    finally:
        db.close()
