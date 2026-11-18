"""One-row table: how outreach email is sent (sender Gmail, footer, on/off)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from acquisition.db.base import Base


class OutreachSettings(Base):
    __tablename__ = "outreach_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    sender_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    optout_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Template sending: when on, the chat sends the saved template without showing an email card.
    auto_send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    first_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    followup_body: Mapped[str | None] = mapped_column(Text, nullable=True)  # no longer used; the stage templates replace it
    stage_templates: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: stage -> email body
    # Automatic follow-up: N days after a person's last email, at this hour (Chicago time), at most M per person.
    # followup_since is set when the switch goes from off to on; only people emailed after it are followed up.
    followup_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    followup_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    followup_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=18, server_default="18")
    followup_max: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    followup_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # One rule per stage (JSON: stage -> enabled, days, hour, max), and limits that apply to every person.
    followup_stages: Mapped[str | None] = mapped_column(Text, nullable=True)
    followup_total_max: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    followup_gap_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
