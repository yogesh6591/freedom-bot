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
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
