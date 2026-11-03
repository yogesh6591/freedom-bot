"""Sending settings: the saved row wins, the OUTREACH_* env values are only the fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.models.outreach_settings import OutreachSettings


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
    row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row
