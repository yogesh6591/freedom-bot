"""Queue one fixed-wording email (a booking link, a payment link) through the same sender as outreach emails.

The wording is written by code, not by the model, so only the link and the person's first name vary. The email
goes out like any other: footer with opt-out and address, spacing and daily ceiling, and a reply inside the
earlier Gmail thread when the person was emailed before.
"""

from __future__ import annotations

from acquisition.config import get_settings
from acquisition.models.prospect import Prospect
from acquisition.services import outreach_service as svc
from app.outreach import sender
from app.outreach.toolkit import Call, ToolError


def sign_off() -> str:
    return (get_settings().default_sender_name or "").strip()


def queue_one(call: Call, prospect: Prospect, subject: str, body: str) -> str:
    """Queue the email and say how it will go out. ``body`` may use ``{{firstName}}``."""
    ready, why = sender.readiness()
    if not ready:
        raise ToolError(f"not queued - {why}.")
    try:
        rows = svc.enqueue(call.db, prospect_ids=[prospect.id], subject_template=subject, body_template=body,
                           requested_by=call.user_id)
    except svc.OutreachError as exc:
        call.db.rollback()
        raise ToolError(f"not queued - {exc}.") from exc
    call.db.commit()
    how = "a reply in their earlier thread" if rows and rows[0].kind == "follow_up" else "a new email"
    return f"queued as {how}; it is sent in the background, not delivered yet"
