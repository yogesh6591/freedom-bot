"""Provider event idempotency and reply conversation models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from acquisition.db.base import Base
from acquisition.models.campaign import ReplyAIStatus, ReplyMessageDirection
from sqlalchemy import Enum


class ProviderEvent(Base):
    __tablename__ = "provider_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    campaign_prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_prospects.id", ondelete="SET NULL"), nullable=True
    )
    payload_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_provider_events_provider_event"),
        Index("ix_provider_events_event_type", "event_type"),
    )


class ReplyConversation(Base):
    __tablename__ = "reply_conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_prospects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[ReplyAIStatus] = mapped_column(
        Enum(
            ReplyAIStatus,
            name="reply_ai_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=ReplyAIStatus.PENDING,
        server_default=ReplyAIStatus.PENDING.value,
    )
    latest_incoming_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_thread_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_provider_email_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list[ReplyMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ReplyMessage.created_at",
    )

    __table_args__ = (Index("ix_reply_conversations_status", "status"),)


class ReplyMessage(Base):
    __tablename__ = "reply_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reply_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[ReplyMessageDirection] = mapped_column(
        Enum(
            ReplyMessageDirection,
            name="reply_message_direction",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
    )
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider_email_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[ReplyConversation] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_reply_messages_conversation_id", "conversation_id"),)
