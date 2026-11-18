"""Sending settings: the saved row wins, the OUTREACH_* env values are only the fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from acquisition.config import get_settings
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


def _in_range(name: str, value, low_high: tuple) -> int:
    low, high = low_high
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number from {low} to {high}") from None
    if not low <= number <= high:
        raise ValueError(f"{name} must be from {low} to {high}")
    return number


def save_followup(
    db: Session,
    *,
    enabled: bool | None = None,
    days: int | None = None,
    hour: int | None = None,
    max_followups: int | None = None,
    updated_by: str | None = None,
) -> OutreachSettings:
    """Change the automatic follow-up rule; only the values given change. Raises ValueError for a value out of range.

    Switching it from off to on starts the clock: only people whose last email goes out after that are followed up."""
    new_days = None if days is None else _in_range("days", days, FOLLOWUP_DAYS)
    new_hour = None if hour is None else _in_range("hour", hour, FOLLOWUP_HOURS)
    new_max = None if max_followups is None else _in_range("max_followups", max_followups, FOLLOWUP_MAX)
    row = get_row(db)
    if row is None:
        row = OutreachSettings(id=1)
        db.add(row)
    if enabled is not None:
        if bool(enabled) and not row.followup_enabled:
            row.followup_since = datetime.now(timezone.utc)
        row.followup_enabled = bool(enabled)
    if new_days is not None:
        row.followup_days = new_days
    if new_hour is not None:
        row.followup_hour = new_hour
    if new_max is not None:
        row.followup_max = new_max
    row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row
