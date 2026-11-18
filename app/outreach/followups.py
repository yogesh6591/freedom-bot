"""Automatic follow-ups: every person has their own clock, and every stage has its own rule.

The admin sets a rule per stage (Integrations page, Sending card, or the chat): "follow up N days after the person's
last email or after they reached this stage, at H o'clock Chicago time, at most M times at this stage". "Send
follow-ups to everyone" in the chat is a separate, manual, instant action and is not touched by this.

* What goes out is exactly what a manual follow-up sends: the saved email for the stage the person is in, in the
  same Gmail thread, signed by the user who sent the earlier email (code picks and fills it; nobody writes it).
* A person's clock starts at the later of their last email and the moment they reached their stage (for example
  opened the assessment), so someone who just did something is not emailed at once because of an old email.
* Only people whose last email went out after the switch was turned on are considered (``followup_since``).
* Limits: each stage has its own "most per person"; on top of that everybody has a total limit of automatic and
  manual follow-ups, and a minimum number of days between any two emails.
* Skipped: people who unsubscribed, bounced or replied, people with an email already waiting, stages switched off,
  people who need no email (booked, paid, result not ready, not a fit), and a payment reminder that is too soon or
  too many. A person whose sender has no connected Gmail waits and goes out once it is connected.
* This module only queues. The sender loop (app/outreach/sender.py) sends, one at a time, with its usual pacing.
"""

from __future__ import annotations

import logging
import time as _time
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import select

from acquisition.models.campaign import OutreachStatus
from acquisition.models.outreach import (KIND_FOLLOW_UP, STATUS_QUEUED, STATUS_SENDING, STATUS_SENT, SentEmail)
from acquisition.models.prospect import Prospect
from acquisition.services import outreach_service as svc
from acquisition.services import outreach_settings
from app.outreach import chicago

log = logging.getLogger(__name__)

#: How often the sender loop looks for people who are due.
CHECK_EVERY_SECONDS = 60
_STOPPED = (OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED, OutreachStatus.REPLIED, OutreachStatus.STOPPED,
            OutreachStatus.COLD)
#: The moment a person reached a stage: the first of these dates on their link that is filled in.
_EVENT_FIELDS = {
    "in_progress": ("assessment_started_at",),
    "qualified": ("assessment_completed_at",),
    "proof": ("proof_viewed_at",),
    "demo": ("demo_started_at",),
    "value": ("demo_completed_at", "demo_watch_completed_at"),
    "calendar": ("calendar_unlocked_at", "value_context_completed_at"),
    "payment_pending": ("checkout_offered_at",),
}
_last_check = 0.0


@dataclass(frozen=True)
class Waiting:
    prospect_id: uuid.UUID
    email: str
    name: str
    due: datetime  # UTC
    requested_by: str
    stage: str  # which saved email goes out (a key of outreach_settings.STAGE_KEYS)
    missing: tuple = ()  # what an in-progress person still has to answer


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_at(last_sent_at: datetime, days: int, hour: int) -> datetime:
    """The moment a person is due: ``days`` calendar days after ``last_sent_at``, at ``hour``:00 Chicago time."""
    day = _aware(last_sent_at).astimezone(chicago.TZ).date() + timedelta(days=int(days))
    return datetime.combine(day, time(int(hour), 0), tzinfo=chicago.TZ).astimezone(timezone.utc)


def clock_text(hour: int) -> str:
    """18 -> '6:00 pm'."""
    return f"{int(hour) % 12 or 12}:00 {'pm' if int(hour) >= 12 else 'am'}"


def stage_event_at(link: Any, stage: str) -> Optional[datetime]:
    """When the person reached this stage, if their link records it."""
    for name in _EVENT_FIELDS.get(stage, ()):
        value = getattr(link, name, None) if link is not None else None
        if value is not None:
            return _aware(value)
    return None


def due_for(last_sent_at: datetime, event_at: Optional[datetime], rule: dict, gap_days: int) -> datetime:
    """When this person is due: the stage's days and hour counted from the later of the last email and the stage
    event, but never sooner than ``gap_days`` after the last email."""
    last = _aware(last_sent_at)
    start = event_at if event_at is not None and event_at > last else last
    return max(due_at(start, rule["days"], rule["hour"]), last + timedelta(days=int(gap_days)))


def rule_line(stage: str, rule: dict) -> str:
    label = outreach_settings.STAGE_LABELS[stage]
    if not rule["enabled"]:
        return f"{label}: off"
    n = int(rule["days"])
    return (f"{label}: {n} day{'s' if n != 1 else ''} at {clock_text(rule['hour'])}, up to {int(rule['max'])}")


def rule_text(cfg: Any) -> str:
    """The whole rule, one line per stage, plus the limits that apply to everybody."""
    lines = [rule_line(k, cfg.followup_stages[k]) for k in outreach_settings.FOLLOWUP_STAGE_KEYS]
    gap = int(cfg.followup_gap_days)
    tail = (f"At most {int(cfg.followup_total_max)} follow-ups per person in total, and at least {gap} "
            f"day{'s' if gap != 1 else ''} between two emails." if gap else
            f"At most {int(cfg.followup_total_max)} follow-ups per person in total.")
    return "; ".join(lines) + ". Times are Chicago time. " + tail


def scan(db: Any, cfg: Any, now: Optional[datetime] = None, *, only_due: bool = False) -> list[Waiting]:
    """Everyone the rule applies to, with when they are due. ``only_due`` keeps just those whose time has come."""
    from acquisition.services.prospect_identity import is_placeholder_email
    from app.outreach import audience as aud
    from acquisition.tenant import prospect_client_clause
    from app.outreach.records import structured_answers_and_missing
    from app.outreach.session import OutreachSession

    if not (cfg.followup_enabled and cfg.followup_since):
        return []
    now = now or datetime.now(timezone.utc)
    since = _aware(cfg.followup_since)
    mails: dict[uuid.UUID, list[SentEmail]] = {}
    fresh = select(SentEmail.prospect_id).where(SentEmail.status == STATUS_SENT, SentEmail.sent_at >= since)
    for e in db.scalars(select(SentEmail).where(SentEmail.prospect_id.in_(fresh),
                                                SentEmail.status.in_((STATUS_SENT, STATUS_QUEUED, STATUS_SENDING)))):
        mails.setdefault(e.prospect_id, []).append(e)

    candidates: list[tuple[uuid.UUID, datetime, str, list[SentEmail]]] = []
    for pid, rows in mails.items():
        if any(r.status in (STATUS_QUEUED, STATUS_SENDING) for r in rows):
            continue  # something is already waiting for this person
        if sum(1 for r in rows if r.kind == KIND_FOLLOW_UP) >= int(cfg.followup_total_max):
            continue
        last = max((r for r in rows if r.sent_at), key=lambda r: _aware(r.sent_at), default=None)
        if last is None or _aware(last.sent_at) < since or not last.requested_by:
            continue
        candidates.append((pid, _aware(last.sent_at), last.requested_by, rows))
    if not candidates:
        return []

    # Only this workspace's people when called from the chat (tenant scope set); everyone for the background job.
    people = {p.id: p for p in db.scalars(select(Prospect).where(
        prospect_client_clause(), Prospect.id.in_([c[0] for c in candidates])))}
    # Everything below is looked up by the person's own record, never by email address, so the same address in
    # two organisations can never be mixed up.
    links, profiles, mail = aud._loaded(db)
    out: list[Waiting] = []
    for pid, last_sent, requester, rows in candidates:
        p = people.get(pid)
        email = str(getattr(p, "email", "") or "").strip()
        if p is None or not email or is_placeholder_email(email) or p.research_status in aud.RESEARCH_HOLD:
            continue
        link = links.get(pid)
        if link is not None and link.outreach_status in _STOPPED:
            continue
        profile = profiles.get(link.id) if link is not None else None
        stage = aud.person_stage(link, profile, (mail.get(pid) or {"count": 0})["count"])
        key = OutreachSession.STAGE_TEMPLATE.get(stage)
        if key is None or key == "unqualified":
            continue  # nothing to send at this stage, or a one-time thank-you that is never repeated
        rule = cfg.followup_stages.get(key)
        if rule is None or not rule["enabled"]:
            continue  # this stage is switched off
        if sum(1 for r in rows if r.kind == KIND_FOLLOW_UP and r.stage == key) >= int(rule["max"]):
            continue  # this stage's limit is used up
        due = due_for(last_sent, stage_event_at(link, key), rule, cfg.followup_gap_days)
        if only_due and due > now:
            continue
        if key == "payment_pending" and OutreachSession._payment_hold(db, pid):
            continue
        missing: tuple = ()
        if key == "in_progress" and profile is not None:
            missing = tuple(structured_answers_and_missing(profile.structured_state)[1])
        out.append(Waiting(pid, email, p.executive_name or "", due, requester, key, missing))
    return out


def _text(db: Any, session: Any, cfg: Any, w: Waiting) -> str:
    """The saved email for this person's stage, filled the same way a manual follow-up is."""
    from app.outreach.session import OutreachSession

    text = cfg.stage_bodies[w.stage].replace("{{senderName}}", session._signer_name(db))
    if "{{next_step_url}}" in text:
        text = text.replace("{{next_step_url}}", OutreachSession._step_url(db, w.prospect_id, w.stage))
    if "{{missingTopics}}" in text:
        text = text.replace("{{missingTopics}}", OutreachSession._missing_phrase(list(w.missing)))
    return OutreachSession._tidy_email(text)


def run(now: Optional[datetime] = None) -> int:
    """Queue the follow-ups that are due. Returns how many were queued."""
    from acquisition.db.session import SessionLocal
    from app.outreach import sender
    from app.outreach.session import OutreachSession
    from app.outreach.text import strip_assessment_link

    db = SessionLocal()
    try:
        cfg = outreach_settings.effective(db)
        if not (cfg.worker_enabled and cfg.followup_enabled and cfg.followup_since):
            return 0
        due = scan(db, cfg, now, only_due=True)
        if not due:
            return 0
        connected = sender._connected_ids()
        due = [w for w in due if w.requested_by in connected]  # the rest wait until that Gmail is connected
        if not due:
            return 0
        subject = strip_assessment_link(cfg.first_subject) or "Following up"
        sessions: dict[str, Any] = {}
        batch, queued = uuid.uuid4(), 0
        for w in due:
            session = sessions.setdefault(w.requested_by, OutreachSession(SimpleNamespace(user_id=w.requested_by), {}))
            try:
                with db.begin_nested():
                    svc.enqueue(db, prospect_ids=[w.prospect_id], batch_id=batch, requested_by=w.requested_by,
                                subject_template=subject, body_template=_text(db, session, cfg, w),
                                automatic=True, stage=w.stage)
                queued += 1
            except svc.OutreachError as exc:
                log.warning("outreach: automatic follow-up skipped for %s: %s", w.email, exc)
        db.commit()
        if queued:
            log.info("outreach: queued %d automatic follow-up(s)", queued)
        return queued
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_if_due() -> int:
    """Called on every pass of the sender loop; does the work at most once a minute."""
    global _last_check
    if _time.monotonic() - _last_check < CHECK_EVERY_SECONDS:
        return 0
    _last_check = _time.monotonic()
    return run()


def describe(db: Any, cfg: Any, rules: bool = True) -> str:
    """Plain text for the chat and the admin page: the rules, whether it is on, and who is waiting.
    The admin page shows the rules in its own table, so it asks for ``rules=False``."""
    shown = f" {rule_text(cfg)}" if rules else ""
    if not cfg.followup_enabled:
        return ("Automatic follow-up is OFF." + (f" The rules saved are:{shown}" if rules else "")
                + " People are emailed only when someone asks in the chat.")
    since = f"People whose last email went out after {chicago.fmt_day(cfg.followup_since)} are covered."
    lines = ["Automatic follow-up is ON. Each stage has its own clock, which starts when the person was last "
             f"emailed or reached that stage.{shown} {since}"]
    if not cfg.worker_enabled:
        lines.append("Sending is switched off on the Integrations page, so nothing goes out until it is on.")
    waiting = scan(db, cfg)
    if waiting:
        first = min(w.due for w in waiting)
        lines.append(f"{len(waiting)} {'person is' if len(waiting) == 1 else 'people are'} waiting for a follow-up; "
                     f"the next is due {chicago.fmt_time(first)} Chicago time.")
    else:
        lines.append("Nobody is waiting for a follow-up right now.")
    return " ".join(lines)
