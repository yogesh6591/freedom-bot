"""Direct outreach: plain emails, one per person, with a full sent-log.

Replaces Instantly. This module knows nothing about Gmail or the chatbot: the
caller passes in a ``send_fn`` (the ops chatbot's Composio Gmail). That keeps the
sending rules (merge, pacing, daily ceiling, log) testable on their own.

Rules
-----
* The AI never types a person's details. The template holds blanks such as
  ``{{firstName}}``; code fills them from the prospect record.
* Every email is stored exactly as sent (subject + body) in ``sent_emails``.
* Each person has ONE permanent assessment link (one hidden "Direct outreach"
  campaign row), reused by follow-ups, so the assessment journey keeps working.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.models.campaign import (
    Campaign,
    CampaignProspect,
    CampaignStatus,
    OutreachStatus,
)
from acquisition.models.outreach import (
    KIND_FIRST,
    KIND_FOLLOW_UP,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_SKIPPED,
    SentEmail,
)
from acquisition.models.prospect import Prospect
from acquisition.services.assessment_tokens import (
    build_assessment_url,
    generate_assessment_token,
    split_executive_name,
)

DIRECT_CAMPAIGN_NAME = "Direct outreach"

_BLANK_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
#: A person may have no last name or company: those blanks render empty (the card warns about it).
OPTIONAL_BLANKS = frozenset({"lastName", "companyName"})
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OutreachError(Exception):
    """A problem the user can fix (blank address, unfilled blank, bad email)."""


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    error: Optional[str] = None


#: (to_email, subject, body, thread_id) -> SendResult
SendFn = Callable[[str, str, str, Optional[str]], SendResult]


# --------------------------------------------------------------------------- pure helpers


def split_name(full_name: str) -> tuple[str, str]:
    return split_executive_name(full_name or "")


def blank_names(template: str) -> set[str]:
    return {m.group(1) for m in _BLANK_RE.finditer(template or "")}


def merge(template: str, values: dict[str, str]) -> str:
    """Fill ``{{blank}}`` slots from ``values``. A blank with no value is an error,
    so a half-filled email can never be sent."""
    unknown = sorted(n for n in blank_names(template) if n not in values)
    missing = sorted(n for n in blank_names(template) if n in values and n not in OPTIONAL_BLANKS
                     and not (values.get(n) or "").strip())
    if unknown or missing:
        raise OutreachError(
            "Cannot fill: " + ", ".join("{{" + n + "}}" for n in unknown + missing)
        )
    out = _BLANK_RE.sub(lambda m: (values.get(m.group(1)) or "").strip(), template)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"[ \t]+([,.!?:;])", r"\1", out)


def without_legacy_footer(body: str, optout_line: str, address: str) -> str:
    """Drop a previously appended opt-out/address suffix; leave any other body unchanged."""
    parts = [p for p in ((optout_line or "").strip(), (address or "").strip()) if p]
    if not parts:
        return body
    suffix = "\n\n--\n" + "\n".join(parts)
    if body.endswith(suffix):
        return body[: -len(suffix)]
    return body


def thread_unreachable(error: Optional[str]) -> bool:
    """True when a send failed only because Gmail could not open the thread (another mailbox, or deleted)."""
    text = (error or "").casefold()
    return "thread" in text and any(w in text for w in ("404", "cannot access", "not found", "does not exist",
                                                       "different gmail", "invalid"))


def can_send_now(
    *,
    sent_today: int,
    ceiling: int,
    last_sent_at: Optional[datetime],
    now: datetime,
    gap_seconds: int,
) -> tuple[bool, str]:
    """(ok, reason). Pure so pacing is testable without a database."""
    if sent_today >= ceiling:
        return False, "daily_ceiling"
    if last_sent_at is not None and (now - last_sent_at).total_seconds() < gap_seconds:
        return False, "gap"
    return True, ""


def local_day_start(now: datetime, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


# --------------------------------------------------------------------------- data access


def ensure_direct_campaign(db: Session) -> Campaign:
    """The one hidden parent every direct-outreach link hangs on."""
    camp = db.scalar(select(Campaign).where(Campaign.name == DIRECT_CAMPAIGN_NAME))
    if camp is None:
        camp = Campaign(
            name=DIRECT_CAMPAIGN_NAME,
            sender_name=get_settings().default_sender_name,
            status=CampaignStatus.READY,
        )
        db.add(camp)
        db.flush()
    return camp


def ensure_link(db: Session, prospect: Prospect) -> CampaignProspect:
    """One permanent assessment link per person (token never changes)."""
    camp = ensure_direct_campaign(db)
    link = db.scalar(
        select(CampaignProspect).where(
            CampaignProspect.campaign_id == camp.id,
            CampaignProspect.prospect_id == prospect.id,
        )
    )
    if link is None:
        link = CampaignProspect(
            campaign_id=camp.id,
            prospect_id=prospect.id,
            assessment_token=generate_assessment_token(),
        )
        db.add(link)
        db.flush()
    elif not link.assessment_token:
        link.assessment_token = generate_assessment_token()
    return link


def last_email_to(db: Session, prospect_id: uuid.UUID) -> Optional[SentEmail]:
    """Most recent email that actually went out (or is waiting) for this person."""
    return db.scalar(
        select(SentEmail)
        .where(
            SentEmail.prospect_id == prospect_id,
            SentEmail.status.in_([STATUS_SENT, STATUS_QUEUED, STATUS_SENDING]),
        )
        .order_by(SentEmail.queued_at.desc())
        .limit(1)
    )


def history_for(db: Session, prospect_id: uuid.UUID) -> list[SentEmail]:
    return list(
        db.scalars(
            select(SentEmail)
            .where(SentEmail.prospect_id == prospect_id)
            .order_by(SentEmail.queued_at.asc())
        )
    )


def merge_values_for(prospect: Prospect, link: CampaignProspect) -> dict[str, str]:
    first, last = split_name(prospect.executive_name)
    return {
        "firstName": (link.first_name_override or first or "").strip() or "there",
        "lastName": (link.last_name_override or last or "").strip(),
        "companyName": (link.company_override or prospect.company or "").strip(),
        "assessment_url": build_assessment_url(link.assessment_token or ""),
    }


# --------------------------------------------------------------------------- enqueue / send


def enqueue(
    db: Session,
    *,
    prospect_ids: Iterable[uuid.UUID],
    subject_template: str,
    body_template: str,
    requested_by: Optional[str] = None,
    batch_id: Optional[uuid.UUID] = None,
    new_thread: bool = False,
) -> list[SentEmail]:
    """Fill the template for each person and queue it. Nothing is sent here.

    Anyone already emailed gets ``kind=follow_up`` linked to their last email and
    reuses the same assessment link, unless ``new_thread`` is true.
    """
    batch = batch_id or uuid.uuid4()
    rows: list[SentEmail] = []
    seen: set[uuid.UUID] = set()
    for pid in prospect_ids:
        if pid in seen:
            continue
        seen.add(pid)
        prospect = db.get(Prospect, pid)
        if prospect is None:
            raise OutreachError(f"Prospect {pid} not found.")
        email = (prospect.email or "").strip()
        if not _EMAIL_RE.match(email):
            raise OutreachError(f"{prospect.executive_name or prospect.company} has no valid email.")
        link = ensure_link(db, prospect)
        if link.outreach_status in (OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED):
            raise OutreachError(
                f"{prospect.executive_name or prospect.company} asked not to be emailed or bounced."
            )
        values = merge_values_for(prospect, link)
        subject = merge(subject_template, values)
        body = merge(body_template, values)
        previous = last_email_to(db, pid)
        follow_up = bool(previous) and not new_thread
        # A thread belongs to one mailbox, so a reply goes into the old thread unless it is known that a DIFFERENT
        # user sent that earlier email; then it is a normal new email. When either sender is not recorded the reply
        # is tried, and the sender falls back to a normal new email if Gmail cannot open the thread.
        threaded = follow_up and (
            not previous.requested_by or not requested_by or previous.requested_by == requested_by)
        if threaded:
            # reply in the earlier Gmail thread: same subject with "Re: "
            base = previous.subject or ""
            subject = base if base.lower().startswith("re:") else f"Re: {base}"[:500]
        row = SentEmail(
            prospect_id=pid,
            campaign_prospect_id=link.id,
            batch_id=batch,
            kind=KIND_FOLLOW_UP if follow_up else KIND_FIRST,
            follow_up_of_id=previous.id if threaded else None,
            to_email=email,
            subject=subject[:500],
            body=body,
            status=STATUS_QUEUED,
            requested_by=requested_by,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _sent_today(db: Session, now: datetime) -> int:
    start = local_day_start(now, get_settings().app_display_timezone)
    return int(
        db.scalar(
            select(func.count()).select_from(SentEmail).where(
                SentEmail.status == STATUS_SENT, SentEmail.sent_at >= start
            )
        )
        or 0
    )


def send_next(
    db: Session, send_fn: SendFn, *, client_id: Optional[str] = None, requested_by: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[SentEmail]:
    """Send at most one queued email if pacing allows. Returns the row handled.

    Safe to call from several workers: the row is claimed with SKIP LOCKED.
    The caller commits.
    """
    cfg = get_settings()
    now = now or datetime.now(timezone.utc)
    last_sent = db.scalar(select(func.max(SentEmail.sent_at)).where(SentEmail.status == STATUS_SENT))
    ok, _reason = can_send_now(
        sent_today=_sent_today(db, now),
        ceiling=cfg.outreach_daily_ceiling,
        last_sent_at=last_sent,
        now=now,
        gap_seconds=cfg.outreach_gap_seconds,
    )
    if not ok:
        return None
    q = select(SentEmail).where(SentEmail.status == STATUS_QUEUED).order_by(SentEmail.queued_at.asc())
    if requested_by:  # only what this user asked for, sent through this user's own mailbox
        q = q.where(SentEmail.requested_by == requested_by)
    elif client_id:
        q = q.join(Prospect, Prospect.id == SentEmail.prospect_id).where(
            or_(Prospect.client_id == client_id, Prospect.client_id.is_(None))
        )
    row = db.scalar(q.with_for_update(skip_locked=True).limit(1))
    if row is None:
        return None
    link = db.get(CampaignProspect, row.campaign_prospect_id) if row.campaign_prospect_id else None
    if link is not None and link.outreach_status in (OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED):
        row.status, row.error = STATUS_SKIPPED, "recipient opted out or bounced"
        return row
    row.attempts += 1
    thread_id = None
    if row.follow_up_of_id:
        prev = db.get(SentEmail, row.follow_up_of_id)
        thread_id = prev.gmail_thread_id if prev else None
    from acquisition.services.outreach_settings import effective
    footer = effective(db)
    row.body = without_legacy_footer(row.body, footer.optout_line, footer.business_address)
    try:
        res = send_fn(row.to_email, row.subject, row.body, thread_id)
    except Exception as exc:  # never lose the row because the connector blew up
        res = SendResult(ok=False, error=str(exc))
    if not res.ok and thread_id and thread_unreachable(res.error):
        # The earlier thread cannot be opened from this mailbox: send a normal new email, not a reply.
        row.follow_up_of_id = None
        if row.subject.lower().startswith("re:"):
            row.subject = row.subject[3:].strip() or row.subject
        try:
            res = send_fn(row.to_email, row.subject, row.body, None)
        except Exception as exc:  # noqa: BLE001
            res = SendResult(ok=False, error=str(exc))
    if res.ok:
        row.status, row.sent_at, row.error = STATUS_SENT, now, None
        row.gmail_message_id, row.gmail_thread_id = res.message_id, res.thread_id
        if link is not None:
            link.outreach_status = OutreachStatus.ACTIVE
            link.last_activity_at = now
    else:
        row.error = (res.error or "send failed")[:2000]
        row.status = STATUS_FAILED if row.attempts >= cfg.outreach_max_attempts else STATUS_QUEUED
    return row


def cancel_batch(db: Session, batch_id: uuid.UUID) -> int:
    """Skip everything in a batch that has not gone out yet."""
    rows = db.scalars(
        select(SentEmail).where(SentEmail.batch_id == batch_id, SentEmail.status == STATUS_QUEUED)
    ).all()
    for r in rows:
        r.status, r.error = STATUS_SKIPPED, "cancelled"
    return len(rows)


def batch_summary(db: Session, batch_id: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for status, n in db.execute(
        select(SentEmail.status, func.count()).where(SentEmail.batch_id == batch_id).group_by(SentEmail.status)
    ):
        out[status] = int(n)
    return out


def eta_minutes(queued: int, sent_today: int) -> int:
    """Rough minutes to clear ``queued`` emails today (gap only; ignores the ceiling)."""
    return round(queued * get_settings().outreach_gap_seconds / 60)


def cancel_all_queued(db: Session) -> int:
    """Stop everything that has not gone out yet ("stop sending")."""
    rows = db.scalars(select(SentEmail).where(SentEmail.status == STATUS_QUEUED)).all()
    for r in rows:
        r.status, r.error = STATUS_SKIPPED, "cancelled"
    return len(rows)


def queued_requesters(db: Session) -> set[str]:
    """The users who have an email waiting to go out."""
    return {
        str(r) for r in db.scalars(select(SentEmail.requested_by).where(SentEmail.status == STATUS_QUEUED).distinct())
        if r
    }


def sent_today(db: Session, now: Optional[datetime] = None) -> int:
    return _sent_today(db, now or datetime.now(timezone.utc))
