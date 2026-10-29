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
    )


def save(
    db: Session,
    *,
    sender_user_id: str,
    business_address: str,
    optout_line: str,
    worker_enabled: bool,
    updated_by: str | None = None,
) -> OutreachSettings:
    row = get_row(db)
    if row is None:
        row = OutreachSettings(id=1)
        db.add(row)
    row.sender_user_id = (sender_user_id or "").strip() or None
    row.business_address = (business_address or "").strip() or None
    row.optout_line = (optout_line or "").strip() or None
    row.worker_enabled = bool(worker_enabled)
    row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row
