"""Conversational CAIO assessment persistence (separate from outreach email threads)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from acquisition.db.base import Base
from acquisition.models.journey import QualificationStatus


class AssessmentMessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


# Backward-compatible aliases — prefer field-entry helpers in journey.py for new code.
EMPTY_STRUCTURED_STATE: dict[str, Any] = {
    "company": None,
    "goals": None,
    "revenue_range": None,
    "headcount": None,
    "functions": None,
    "tools": None,
    "workflows": None,
    "knowledge_locations": None,
    "pain_points": None,
    "time_cost_impact": None,
    "bottlenecks": None,
    "change_readiness": None,
    "ai_readiness": None,
    "authority": None,
}

EMPTY_QUALIFICATION: dict[str, Any] = {
    "icp_fit": None,
    "complexity": None,
    "real_money_problem": None,
    "authority": None,
    "growth_change_readiness": None,
    "openness_to_ai": None,
    "economic_capacity": None,
    "budget_readiness": "not_assessed",
    "ability_to_pay": None,
    "notes": None,
    "explanation": None,
    "status": QualificationStatus.PENDING.value,
    "assessment_status": QualificationStatus.PENDING.value,
}


class AssessmentConversation(Base):
    __tablename__ = "assessment_conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_prospects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list[AssessmentMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AssessmentMessage.created_at",
    )
    profile: Mapped[AssessmentProfile | None] = relationship(
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_assessment_conversations_campaign_prospect_id", "campaign_prospect_id"),
    )


class AssessmentMessage(Base):
    __tablename__ = "assessment_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[AssessmentMessageRole] = mapped_column(
        Enum(
            AssessmentMessageRole,
            name="assessment_message_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[AssessmentConversation] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_assessment_messages_conversation_id", "conversation_id"),)


class AssessmentProfile(Base):
    """Structured state, stored result, and qualification foundation for one assessment."""

    __tablename__ = "assessment_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_prospects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    structured_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    qualification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    personalized_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    personalized_value_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pre_call_brief_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pre_call_brief_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pre_call_brief_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pre_call_brief_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pre_call_brief_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qualification_status: Mapped[QualificationStatus] = mapped_column(
        Enum(
            QualificationStatus,
            name="qualification_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=QualificationStatus.PENDING,
        server_default=QualificationStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversation: Mapped[AssessmentConversation] = relationship(back_populates="profile")

    __table_args__ = (
        UniqueConstraint("campaign_prospect_id", name="uq_assessment_profiles_link"),
        Index("ix_assessment_profiles_conversation_id", "conversation_id"),
    )
