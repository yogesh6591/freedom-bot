"""Who is in an audience: one query over prospects, journey state and email history.

The model only says what it wants (stage, newest or oldest N, emailed or not, how long quiet, a word or a place);
this module finds the people, so the list is always real and complete.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acquisition.models.assessment import AssessmentProfile
from acquisition.models.campaign import AssessmentStatus, CampaignProspect, JourneyStage, OutreachStatus
from acquisition.models.journey import QualificationStatus
from acquisition.models.outreach import STATUS_QUEUED, STATUS_SENT, SentEmail
from acquisition.models.prospect import Prospect
from acquisition.tenant import prospect_client_clause
from acquisition.services import outreach_service as svc
from acquisition.services.prospect_identity import is_placeholder_email
from app.outreach.records import structured_answers_and_missing

#: Where a person is, from first contact to purchase. One value per person.
STAGES = ("new", "not_started", "in_progress", "completed", "qualified", "proof", "demo", "value", "calendar",
          "unqualified", "booked", "payment_pending", "purchased")
#: Older or looser words that mean several stages.
STAGE_ALIASES = {"exploring": ("proof", "demo", "value", "calendar")}
_STEP_STAGE = {"proof": "proof", "demo": "demo", "value": "value", "calendar_unlocked": "calendar"}
RESEARCH_HOLD = ("queued", "running")
ORDERS = ("newest", "oldest")
EMAILED = ("yes", "no", "any")
RECENT_DAYS = 0  # someone emailed in the last 2 days is skipped unless include_recent is set

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OPTED_OUT = (OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED)
_EXPLORING = (JourneyStage.PROOF, JourneyStage.DEMO, JourneyStage.VALUE, JourneyStage.CALENDAR_UNLOCKED)


def _val(x: Any) -> str:
    return str(getattr(x, "value", x) or "")


def person_stage(link: Optional[CampaignProspect], profile: Optional[AssessmentProfile], emails: int) -> str:
    """The one stage a person is in right now (the furthest point they reached)."""
    if link is not None:
        if _val(link.purchase_outcome) == "purchased":
            return "purchased"
        if link.checkout_offered_at is not None:
            return "payment_pending"  # an offer was made and it is not paid
        if link.journey_stage == JourneyStage.BOOKED or _val(link.booking_status) not in ("", "not_booked"):
            return "booked"
        if _val(link.journey_stage) in _STEP_STAGE:
            return _STEP_STAGE[_val(link.journey_stage)]
        qual = profile.qualification_status if profile is not None else None
        if qual == QualificationStatus.QUALIFIED or link.journey_stage == JourneyStage.QUALIFIED:
            return "qualified"
        if qual == QualificationStatus.UNQUALIFIED or link.journey_stage in (
                JourneyStage.UNQUALIFIED, JourneyStage.UNQUALIFIED_EXIT):
            return "unqualified"
        if link.assessment_status == AssessmentStatus.COMPLETED:
            return "completed"
        if link.assessment_status == AssessmentStatus.STARTED:
            return "in_progress"
    return "not_started" if emails > 0 else "new"


def _loaded(db: Session) -> tuple[dict, dict, dict]:
    """Links, assessment profiles and live emails for everyone, in three queries."""
    camp = db.scalar(select(svc.Campaign).where(svc.Campaign.name == svc.DIRECT_CAMPAIGN_NAME))
    links: dict[uuid.UUID, CampaignProspect] = {}
    for row in db.scalars(select(CampaignProspect).order_by(CampaignProspect.created_at)):
        keep = links.get(row.prospect_id)
        direct = camp is not None and row.campaign_id == camp.id
        if keep is None or direct or not (camp is not None and keep.campaign_id == camp.id):
            links[row.prospect_id] = row
    profiles = {p.campaign_prospect_id: p for p in db.scalars(select(AssessmentProfile))}
    mail: dict[uuid.UUID, dict] = {}
    for e in db.scalars(select(SentEmail).where(SentEmail.status.in_((STATUS_SENT, STATUS_QUEUED)))):
        m = mail.setdefault(e.prospect_id, {"count": 0, "last": None})
        m["count"] += 1
        at = e.sent_at or e.queued_at
        if at is not None and (m["last"] is None or at > m["last"]):
            m["last"] = at
    return links, profiles, mail


def _place_text(p: Prospect) -> str:
    return " ".join(str(x) for x in (p.city, p.state, p.region, p.locations_json or "") if x).lower()


def _aware(d: Optional[datetime]) -> Optional[datetime]:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def select_people(db: Session, *, stage: Optional[list] = None, order: str = "", count: int = 0,
                  emailed: str = "any", inactive_days: int = 0, text: str = "", place: str = "",
                  include_recent: bool = False, now: Optional[datetime] = None) -> dict:
    """The people matching what was asked.

    Returns {"people": [...], "skipped": [...], "error": str}. ``error`` is set when the question cannot be
    answered honestly (an unknown stage, or a place filter when nobody has a place saved).
    """
    now = now or datetime.now(timezone.utc)
    stages = []
    for s in (stage or []):
        s = str(s).strip().lower()
        if s:
            stages.extend(STAGE_ALIASES.get(s, (s,)))
    bad = [s for s in stages if s not in STAGES]
    if bad:
        return {"people": [], "skipped": [], "error": f"unknown stage {', '.join(bad)}. Stages: {', '.join(STAGES)}"}
    if order and order not in ORDERS:
        return {"people": [], "skipped": [], "error": "order must be newest or oldest"}
    if emailed not in EMAILED:
        return {"people": [], "skipped": [], "error": "emailed must be yes, no or any"}

    prospects = list(
        db.scalars(select(Prospect).where(prospect_client_clause()).order_by(Prospect.created_at, Prospect.executive_name))
    )
    place_q = (place or "").strip().lower()
    if place_q and not any(_place_text(p) for p in prospects):
        return {"people": [], "skipped": [], "error": "no_location_data: no saved person has a city, state or region yet, "
                                                         "so people cannot be picked by place. Nothing was selected."}
    links, profiles, mail = _loaded(db)
    word = (text or "").strip().lower()
    people: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for p in prospects:
        email = str(p.email or "").strip()
        label = p.executive_name or p.company or email
        link = links.get(p.id)
        m = mail.get(p.id) or {"count": 0, "last": None}
        profile = profiles.get(link.id) if link is not None else None
        st = person_stage(link, profile, m["count"])
        # the asked-for filters come first, so "skipped" only ever lists people who would have matched
        if stages and st not in stages:
            continue
        if emailed == "yes" and m["count"] == 0 or emailed == "no" and m["count"] > 0:
            continue
        if word and word not in " ".join(str(x or "") for x in (p.executive_name, p.company, p.email, p.industry)).lower():
            continue
        if place_q and place_q not in _place_text(p):
            continue
        touches = [d for d in (_aware(m["last"]), _aware(link.last_activity_at if link else None)) if d is not None]
        last_touch = max(touches) if touches else (_aware(p.created_at) or now)  # quiet since the last contact
        quiet = (now - last_touch).days
        if inactive_days and quiet < inactive_days:
            continue
        if not _EMAIL.match(email):
            skipped.append({"name": label, "email": email, "reason": "no valid email"})
            continue
        if is_placeholder_email(email):
            skipped.append({"name": label, "email": "", "reason": "no email yet"})
            continue
        if p.research_status in RESEARCH_HOLD:
            skipped.append({"name": label, "email": email, "reason": "still being researched"})
            continue
        if p.research_status == "done" and (p.research_notes or "").startswith("Verdict: not suitable"):
            skipped.append({"name": label, "email": email, "reason": "researched: not suitable (name them to email anyway)"})
            continue
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        if link is not None and link.outreach_status in _OPTED_OUT:
            skipped.append({"name": label, "email": email, "reason": _val(link.outreach_status)})
            continue
        if m["last"] is not None and not include_recent and (now - _aware(m["last"])) < timedelta(days=RECENT_DAYS):
            skipped.append({"name": label, "email": email, "reason": f"emailed in the last {RECENT_DAYS} days"})
            continue
        row = {"id": str(p.id), "email": email, "name": p.executive_name or "", "company": (p.company or "").strip(),
               "stage": st, "emails": m["count"], "days_quiet": quiet, "added": _aware(p.created_at),
               "offered_at": link.checkout_offered_at if link is not None else None,
               "priority": float(p.priority_score) if p.priority_score is not None else None}
        if st in ("in_progress", "not_started") and profile is not None:
            got, missing = structured_answers_and_missing(profile.structured_state)
            row["captured"] = [f"{g['key']}: {str(g['value'])[:80]}" for g in got][:6]
            row["missing"] = missing
        people.append(row)

    if order:
        people.sort(key=lambda r: r["added"], reverse=(order == "newest"))
        n = count if count and count > 0 else 1
        people = people[:n]
    else:
        # nobody was named and no order was asked: the best fit goes first, researched before unresearched
        people.sort(key=lambda r: (-(r["priority"] if r["priority"] is not None else -1.0), r["added"]))
        if count and count > 0:
            people = people[:count]
    return {"people": people, "skipped": skipped, "error": ""}


def held_among(db: Session, emails: list) -> set:
    """Lower-case emails (of the given ones) whose person is queued or being researched, so cannot be emailed."""
    keys = {str(e).strip().lower() for e in emails if e}
    if not keys:
        return set()
    rows = db.scalars(
        select(Prospect.email).where(
            prospect_client_clause(),
            func.lower(Prospect.email).in_(keys),
            Prospect.research_status.in_(RESEARCH_HOLD),
        )
    )
    return {str(e).strip().lower() for e in rows}


def stages_for(db: Session, emails: list) -> dict:
    """email (lower case) -> stage, for the given people."""
    keys = {str(e).strip().lower() for e in emails if e}
    if not keys:
        return {}
    links, profiles, mail = _loaded(db)
    out: dict[str, str] = {}
    for p in db.scalars(select(Prospect).where(prospect_client_clause(), func.lower(Prospect.email).in_(keys))):
        link = links.get(p.id)
        profile = profiles.get(link.id) if link is not None else None
        out[str(p.email).strip().lower()] = person_stage(link, profile, (mail.get(p.id) or {"count": 0})["count"])
    return out


def describe(result: dict, sample: int = 10) -> str:
    """Plain text for the model: how many, by stage, and a few rows with what they have done."""
    if result.get("error"):
        return "error: " + result["error"]
    people, skipped = result["people"], result["skipped"]
    if not people:
        why = f" {len(skipped)} were skipped ({'; '.join(sorted({s['reason'] for s in skipped}))})." if skipped else ""
        return "nobody matches." + why
    by: dict[str, int] = {}
    for r in people:
        by[r["stage"]] = by.get(r["stage"], 0) + 1
    lines = [f"{len(people)} match: " + ", ".join(f"{n} {s}" for s, n in by.items()) + "."]
    for r in people[:sample]:
        extra = ""
        if r.get("captured") is not None:
            extra = f"; answered: {', '.join(r['captured']) or 'nothing'}; still missing: {', '.join(r['missing'][:5]) or 'nothing'}"
        lines.append(f"- {r['name'] or r['email']} ({r['company'] or 'no company'}): {r['stage']}, "
                     f"{r['emails']} email(s), quiet {r['days_quiet']} day(s){extra}")
    if len(people) > sample:
        lines.append(f"... and {len(people) - sample} more.")
    if skipped:
        lines.append(f"{len(skipped)} skipped: " + "; ".join(sorted({s['reason'] for s in skipped})) + ".")
    return "\n".join(lines)
