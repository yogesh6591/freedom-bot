"""Sending settings: the saved row wins, the OUTREACH_* env values are only the fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.models.outreach_settings import OutreachSettings


#: The email each person gets by where they are. Code fills the blanks; nobody writes these again unless an admin edits them.
STAGE_KEYS = ("not_started", "in_progress", "qualified", "unqualified")
STAGE_LABELS = {
    "not_started": "Emailed, has not started the assessment",
    "in_progress": "Started the assessment, not finished",
    "qualified": "Finished and a good fit, not booked",
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
        "Hi {{firstName}},\n\nThanks for completing the assessment. The next step is a short call with Jeanne to "
        "go through your results. You can pick a time here:\n\n{{assessment_url}}\n\nBest,\n{{senderName}}"),
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
