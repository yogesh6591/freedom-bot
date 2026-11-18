"""Sending settings: the saved row wins, the OUTREACH_* env values are only the fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.models.outreach import STATUS_QUEUED, STATUS_SKIPPED, SentEmail
from acquisition.models.outreach_settings import OutreachSettings


#: The email each person gets by where they are. Code fills the blanks; nobody writes these again unless an admin edits them.
STAGE_KEYS = ("not_started", "in_progress", "qualified", "proof", "demo", "value", "calendar", "payment_pending",
              "unqualified")
STAGE_LABELS = {
    "not_started": "Emailed, has not started the assessment",
    "in_progress": "Started the assessment, not finished",
    "qualified": "Finished and a good fit, has not opened the next step",
    "proof": "Looking at examples, not finished",
    "demo": "At the demo, not finished",
    "value": "At the value summary, not finished",
    "calendar": "Ready to book a call, not booked",
    "payment_pending": "Payment offered, not paid (reminder)",
    "unqualified": "Finished, not a fit",
}
DEFAULT_STAGE_TEMPLATES = {
    "not_started": (
        "Hi {{firstName}},\n\nJust a quick follow-up. The assessment takes a few minutes and gives you a clear "
        "summary of where AI could save your team time.\n\n{{assessment_url}}\n\nBest,\n{{senderName}}"),
    "in_progress": (
        "Hi {{firstName}},\n\nYou are partway through the assessment. It only needs {{missingTopics}} to finish. "
        "Pick up where you left off:\n\n{{assessment_url}}\n\nBest,\n{{senderName}}"),
    "qualified": (
        "Hi {{firstName}},\n\nThanks for completing the assessment. Your results are ready, and the next step is "
        "a short walkthrough of how we would help your team.\n\n{{next_step_url}}\n\nBest,\n{{senderName}}"),
    "proof": (
        "Hi {{firstName}},\n\nYou were looking at examples of how we have helped teams like yours. It only takes "
        "a few minutes to go through the rest.\n\n{{next_step_url}}\n\nBest,\n{{senderName}}"),
    "demo": (
        "Hi {{firstName}},\n\nYou are one step away from the demo. It is short and shows how this would work for "
        "your team.\n\n{{next_step_url}}\n\nBest,\n{{senderName}}"),
    "value": (
        "Hi {{firstName}},\n\nThe last part is a short summary of what this could be worth to your team. It takes "
        "a couple of minutes.\n\n{{next_step_url}}\n\nBest,\n{{senderName}}"),
    "calendar": (
        "Hi {{firstName}},\n\nYou are ready for the next step, a short call with Jeanne to go through your "
        "results. You can pick a time here:\n\n{{next_step_url}}\n\nBest,\n{{senderName}}"),
    "payment_pending": (
        "Hi {{firstName}},\n\nFollowing up on your call with Jeanne. When you are ready, you can complete your "
        "payment securely here:\n\n{{next_step_url}}\n\nBest,\n{{senderName}}"),
    "unqualified": (
        "Hi {{firstName}},\n\nThank you for taking the time to complete the assessment. We appreciate your "
        "interest and wish you the best.\n\nBest,\n{{senderName}}"),
}


def _stage_bodies(raw: Optional[str]) -> dict:
    """Saved bodies over the defaults. A stage with nothing saved uses the default."""
    out = dict(DEFAULT_STAGE_TEMPLATES)
    try:
        saved = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        for k in STAGE_KEYS:
            if isinstance(saved.get(k), str) and saved[k].strip():
                out[k] = saved[k].strip()
    return out


#: The stages automatic follow-up can email ("finished, not a fit" is a one-time thank-you, never repeated).
FOLLOWUP_STAGE_KEYS = tuple(k for k in STAGE_KEYS if k != "unqualified")


def _stage_rules(raw: Optional[str], days: int, hour: int, most: int) -> dict:
    """stage -> {enabled, days, hour, max}. A stage with nothing saved uses the given defaults."""
    rules = {k: {"enabled": True, "days": days, "hour": hour, "max": most} for k in FOLLOWUP_STAGE_KEYS}
    rules["payment_pending"]["days"] = max(days, 3)  # a payment reminder is never sooner than 3 days anyway
    rules["payment_pending"]["max"] = 3  # the payment rule allows at most 3 reminders
    try:
        saved = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        for k in FOLLOWUP_STAGE_KEYS:
            one = saved.get(k)
            if not isinstance(one, dict):
                continue
            if isinstance(one.get("enabled"), bool):
                rules[k]["enabled"] = one["enabled"]
            for field_name, span in (("days", FOLLOWUP_DAYS), ("hour", FOLLOWUP_HOURS), ("max", FOLLOWUP_MAX)):
                v = one.get(field_name)
                if isinstance(v, int) and not isinstance(v, bool) and span[0] <= v <= span[1]:
                    rules[k][field_name] = v
    return rules


@dataclass(frozen=True)
class Effective:
    sender_user_id: str
    business_address: str
    optout_line: str
    worker_enabled: bool
    saved: bool  # a row exists in the database
    auto_send_enabled: bool = False
    first_subject: str = ""
    first_body: str = ""
    followup_body: str = ""
    stage_bodies: dict = field(default_factory=lambda: dict(DEFAULT_STAGE_TEMPLATES))
    followup_enabled: bool = False
    followup_days: int = 2
    followup_hour: int = 18
    followup_max: int = 2
    followup_since: Optional[datetime] = None
    followup_stages: dict = field(default_factory=lambda: _stage_rules(None, 2, 18, 2))
    followup_total_max: int = 6
    followup_gap_days: int = 1

    @property
    def template_ready(self) -> bool:
        """Template sending needs a first-email subject and body."""
        return bool(self.first_subject.strip() and self.first_body.strip())


def get_row(db: Session) -> Optional[OutreachSettings]:
    return db.get(OutreachSettings, 1)


def effective(db: Session) -> Effective:
    cfg = get_settings()
    row = get_row(db)
    if row is None:
        return Effective(
            sender_user_id=(cfg.outreach_sender_user_id or "").strip(),
            business_address=(cfg.outreach_business_address or "").strip(),
            optout_line=(cfg.outreach_optout_line or "").strip(),
            worker_enabled=bool(cfg.outreach_worker_enabled),
            saved=False,
        )
    return Effective(
        sender_user_id=(row.sender_user_id or cfg.outreach_sender_user_id or "").strip(),
        business_address=(row.business_address or cfg.outreach_business_address or "").strip(),
        optout_line=(row.optout_line or cfg.outreach_optout_line or "").strip(),
        worker_enabled=bool(row.worker_enabled),
        saved=True,
        auto_send_enabled=bool(row.auto_send_enabled),
        first_subject=(row.first_subject or "").strip(),
        first_body=(row.first_body or "").strip(),
        followup_body=(row.followup_body or "").strip(),
        stage_bodies=_stage_bodies(row.stage_templates),
        followup_enabled=bool(row.followup_enabled),
        followup_days=int(row.followup_days or 2),
        followup_hour=18 if row.followup_hour is None else int(row.followup_hour),
        followup_max=int(row.followup_max or 2),
        followup_since=row.followup_since,
        followup_stages=_stage_rules(row.followup_stages, int(row.followup_days or 2),
                                     18 if row.followup_hour is None else int(row.followup_hour),
                                     int(row.followup_max or 2)),
        followup_total_max=int(row.followup_total_max or 6),
        followup_gap_days=1 if row.followup_gap_days is None else int(row.followup_gap_days),
    )


def save(
    db: Session,
    *,
    sender_user_id: str,
    business_address: str,
    optout_line: str,
    worker_enabled: bool,
    updated_by: str | None = None,
    auto_send_enabled: bool | None = None,
    first_subject: str | None = None,
    first_body: str | None = None,
    followup_body: str | None = None,
    stage_bodies: dict | None = None,
) -> OutreachSettings:
    """Save the sending settings. The toggle and template fields change only when a value is given."""
    row = get_row(db)
    if row is None:
        row = OutreachSettings(id=1)
        db.add(row)
    row.sender_user_id = (sender_user_id or "").strip() or None
    row.business_address = (business_address or "").strip() or None
    row.optout_line = (optout_line or "").strip() or None
    row.worker_enabled = bool(worker_enabled)
    if auto_send_enabled is not None:
        row.auto_send_enabled = bool(auto_send_enabled)
    if first_subject is not None:
        row.first_subject = first_subject.strip() or None
    if first_body is not None:
        row.first_body = first_body.strip() or None
    if followup_body is not None:
        row.followup_body = followup_body.strip() or None
    if stage_bodies is not None:
        merged = _stage_bodies(row.stage_templates)
        for k, v in stage_bodies.items():
            if k in STAGE_KEYS:
                merged[k] = (v or "").strip() or DEFAULT_STAGE_TEMPLATES[k]  # empty means back to the default
        row.stage_templates = json.dumps(merged)
    row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


FOLLOWUP_DAYS = (1, 30)
FOLLOWUP_HOURS = (0, 23)
FOLLOWUP_MAX = (1, 10)
FOLLOWUP_TOTAL = (1, 20)
FOLLOWUP_GAP = (0, 7)


def _in_range(name: str, value, low_high: tuple) -> int:
    low, high = low_high
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number from {low} to {high}") from None
    if not low <= number <= high:
        raise ValueError(f"{name} must be from {low} to {high}")
    return number


def _check_stage(stage) -> str:
    key = str(stage or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in FOLLOWUP_STAGE_KEYS:
        raise ValueError("stage must be one of: " + ", ".join(FOLLOWUP_STAGE_KEYS))
    return key


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _cancel_waiting(db: Session, stage: str | None = None) -> int:
    """Cancel the automatic follow-ups that are still waiting to go out (all, or one stage's)."""
    query = select(SentEmail).where(SentEmail.status == STATUS_QUEUED, SentEmail.automatic.is_(True))
    if stage is not None:
        query = query.where(SentEmail.stage == stage)
    rows = db.scalars(query).all()
    for r in rows:
        r.status, r.error = STATUS_SKIPPED, "automatic follow-up switched off"
    return len(rows)


def _apply(db: Session, master: bool | None, changes: dict, total_max: int | None, gap_days: int | None,
           updated_by: str | None) -> OutreachSettings:
    """Write checked values. Nothing is touched before this point, so a refused value changes nothing."""
    row = get_row(db)
    if row is None:
        row = OutreachSettings(id=1)
        db.add(row)
    rules = _stage_rules(row.followup_stages, int(row.followup_days or 2),
                         18 if row.followup_hour is None else int(row.followup_hour), int(row.followup_max or 2))
    cancelled = 0
    for key, rule in changes.items():
        rules[key] = rule
        if not rule["enabled"]:
            cancelled += _cancel_waiting(db, key)
    if changes:
        row.followup_stages = json.dumps(rules)
        first = rules["not_started"]  # the old single rule is kept in step, only for a rollback
        row.followup_days, row.followup_hour, row.followup_max = first["days"], first["hour"], first["max"]
    if total_max is not None:
        row.followup_total_max = total_max
    if gap_days is not None:
        row.followup_gap_days = gap_days
    if master is not None:
        now = datetime.now(timezone.utc)
        if master and not row.followup_enabled:
            if row.followup_since is None:
                row.followup_since = now  # the very first time: only people emailed from now on
            else:
                # Switched back on: keep people who are still in the middle of a sequence, do not revive old chats.
                widest = max([r["days"] for r in rules.values() if r["enabled"]] or [2])
                row.followup_since = max(_aware(row.followup_since), now - timedelta(days=widest + 1))
        if not master and row.followup_enabled:
            cancelled += _cancel_waiting(db)  # switching off also cancels what is already waiting
        row.followup_enabled = bool(master)
    row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    row.cancelled_count = cancelled  # not a column: how many waiting follow-ups this change cancelled
    return row


def save_followup_rules(
    db: Session,
    *,
    enabled: bool | None = None,
    rules: dict | None = None,
    total_max: int | None = None,
    gap_days: int | None = None,
    updated_by: str | None = None,
) -> OutreachSettings:
    """Save the whole table from the admin page: rules is {stage: {enabled, days, hour, max}}. Every value is checked
    first; raises ValueError (and changes nothing) when one is out of range."""
    checked = {}
    for key, one in (rules or {}).items():
        checked[_check_stage(key)] = {
            "enabled": bool(one.get("enabled", True)),
            "days": _in_range("days", one.get("days"), FOLLOWUP_DAYS),
            "hour": _in_range("hour", one.get("hour"), FOLLOWUP_HOURS),
            "max": _in_range("max_followups", one.get("max"), FOLLOWUP_MAX),
        }
    new_total = None if total_max is None else _in_range("total_max", total_max, FOLLOWUP_TOTAL)
    new_gap = None if gap_days is None else _in_range("gap_days", gap_days, FOLLOWUP_GAP)
    return _apply(db, enabled, checked, new_total, new_gap, updated_by)


def save_followup(
    db: Session,
    *,
    enabled: bool | None = None,
    days: int | None = None,
    hour: int | None = None,
    max_followups: int | None = None,
    stage: str | None = None,
    total_max: int | None = None,
    gap_days: int | None = None,
    updated_by: str | None = None,
) -> OutreachSettings:
    """Change the automatic follow-up rules; only the values given change. Raises ValueError for a bad value.

    With ``stage`` the change is for that stage only (``enabled`` then switches that stage on or off); without it
    days, hour and limit apply to every stage and ``enabled`` is the master switch. Switching the master switch off
    cancels follow-ups still waiting to go out; switching it on again keeps people who are mid-sequence."""
    new_days = None if days is None else _in_range("days", days, FOLLOWUP_DAYS)
    new_hour = None if hour is None else _in_range("hour", hour, FOLLOWUP_HOURS)
    new_max = None if max_followups is None else _in_range("max_followups", max_followups, FOLLOWUP_MAX)
    new_total = None if total_max is None else _in_range("total_max", total_max, FOLLOWUP_TOTAL)
    new_gap = None if gap_days is None else _in_range("gap_days", gap_days, FOLLOWUP_GAP)
    key = None if stage is None else _check_stage(stage)
    row = get_row(db)
    current = _stage_rules(row.followup_stages if row else None, int(row.followup_days or 2) if row else 2,
                           (18 if row.followup_hour is None else int(row.followup_hour)) if row else 18,
                           int(row.followup_max or 2) if row else 2)
    changes = {}
    for k in ((key,) if key else FOLLOWUP_STAGE_KEYS):
        rule = dict(current[k])
        if new_days is not None:
            rule["days"] = new_days
        if new_hour is not None:
            rule["hour"] = new_hour
        if new_max is not None:
            rule["max"] = new_max
        if key is not None and enabled is not None:
            rule["enabled"] = bool(enabled)
        if rule != current[k]:
            changes[k] = rule
    return _apply(db, enabled if key is None else None, changes, new_total, new_gap, updated_by)
