"""Outreach log — one row per email we send (or have queued) to a prospect."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from acquisition.db.base import Base

# Plain strings (not DB enums) so adding a state never needs a type migration.
KIND_FIRST = "first"
KIND_FOLLOW_UP = "follow_up"
STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


class SentEmail(Base):
    __tablename__ = "sent_emails"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False
    )
    campaign_prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_prospects.id", ondelete="SET NULL"), nullable=True
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(nullable=False, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=KIND_FIRST)
    follow_up_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sent_emails.id", ondelete="SET NULL"), nullable=True
    )
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Exact text that was (or will be) sent, already filled in for this person.
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_QUEUED)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_sent_emails_prospect_id", "prospect_id"),
        Index("ix_sent_emails_status_queued", "status", "queued_at"),
        Index("ix_sent_emails_batch_id", "batch_id"),
        Index("ix_sent_emails_sent_at", "sent_at"),
    )
