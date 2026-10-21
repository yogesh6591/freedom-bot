"""Campaign domain models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from acquisition.db.base import Base

if TYPE_CHECKING:
    from acquisition.models.prospect import Prospect


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class MessageType(str, enum.Enum):
    INITIAL = "initial"
    FOLLOW_UP = "follow_up"


class MessageChannel(str, enum.Enum):
    EMAIL = "email"
    # Future: SMS = "sms"


class InstantlySyncStatus(str, enum.Enum):
    NOT_SYNCED = "not_synced"
    SYNCED = "synced"
    SYNC_ERROR = "sync_error"


class InstantlyRuntimeStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    PAUSED = "paused"


class LeadSyncStatus(str, enum.Enum):
    NOT_SYNCED = "not_synced"
    SYNCED = "synced"
    SYNC_ERROR = "sync_error"


class OutreachStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REPLIED = "replied"
    ASSESSMENT_STARTED = "assessment_started"
    COMPLETED = "completed"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    COLD = "cold"
    STOPPED = "stopped"


class AssessmentStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    COMPLETED = "completed"


class JourneyStage(str, enum.Enum):
    """Acquisition path on CampaignProspect (assessment → proof → demo → value → calendar)."""

    OUTREACH = "outreach"
    ASSESSMENT_STARTED = "assessment_started"
    ASSESSMENT_COMPLETED = "assessment_completed"
    QUALIFIED = "qualified"  # assessment-qualified; at result, may continue to proof
    UNQUALIFIED = "unqualified"  # legacy alias; prefer UNQUALIFIED_EXIT
    UNQUALIFIED_EXIT = "unqualified_exit"
    PENDING_REVIEW = "pending_review"
    PROOF = "proof"
    DEMO = "demo"
    VALUE = "value"
    CALENDAR_UNLOCKED = "calendar_unlocked"
    BOOKED = "booked"


class ColdStatus(str, enum.Enum):
    NOT_COLD = "not_cold"
    COLD = "cold"


class ConsultationShowStatus(str, enum.Enum):
    """Admin-recorded consultation attendance (not public)."""

    NOT_RECORDED = "not_recorded"
    SHOWED = "showed"
    NO_SHOW = "no_show"


class PurchaseOutcome(str, enum.Enum):
    """Purchase outcome — set by JeanneCAIO Stripe checkout or admin override."""

    NOT_RECORDED = "not_recorded"
    PURCHASED = "purchased"
    DID_NOT_PURCHASE = "did_not_purchase"


class HandoffStatus(str, enum.Enum):
    """Post-purchase delivery handoff to Cepoch (JeanneCAIO → white-label build)."""

    NOT_STARTED = "not_started"
    READY = "ready"  # paid / committed — ready for Cepoch onboarding
    HANDED_OFF = "handed_off"  # confirmed into Cepoch delivery


class ReplyAIStatus(str, enum.Enum):
    PENDING = "pending"
    AUTO_REPLIED = "auto_replied"
    NEEDS_REVIEW = "needs_review"
    STOPPED = "stopped"
    ASSESSMENT_STARTED = "assessment_started"
    SKIPPED_AUTO_REPLY = "skipped_auto_reply"


class ReplyMessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    assessment_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(
            CampaignStatus,
            name="campaign_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=CampaignStatus.DRAFT,
        server_default=CampaignStatus.DRAFT.value,
    )
    # Development defaults only — admin-editable Instantly campaign_schedule fields.
    send_timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="America/Chicago",
        server_default="America/Chicago",
    )
    send_window_start: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="00:00",
        server_default="00:00",
    )
    send_window_end: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="10:30",
        server_default="10:30",
    )
    send_monday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    send_tuesday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    send_wednesday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    send_thursday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    send_friday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    send_saturday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    send_sunday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    instantly_campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    instantly_sync_status: Mapped[InstantlySyncStatus] = mapped_column(
        Enum(
            InstantlySyncStatus,
            name="instantly_sync_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=InstantlySyncStatus.NOT_SYNCED,
        server_default=InstantlySyncStatus.NOT_SYNCED.value,
    )
    instantly_last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    instantly_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    instantly_email_list: Mapped[str | None] = mapped_column(Text, nullable=True)
    instantly_runtime_status: Mapped[InstantlyRuntimeStatus] = mapped_column(
        Enum(
            InstantlyRuntimeStatus,
            name="instantly_runtime_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=InstantlyRuntimeStatus.NOT_STARTED,
        server_default=InstantlyRuntimeStatus.NOT_STARTED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list[CampaignMessage]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="CampaignMessage.sequence_order",
    )
    campaign_prospects: Mapped[list[CampaignProspect]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_campaigns_status", "status"),
        Index("ix_campaigns_created_at", "created_at"),
        Index("ix_campaigns_name", "name"),
        Index("ix_campaigns_instantly_campaign_id", "instantly_campaign_id"),
        Index("ix_campaigns_instantly_sync_status", "instantly_sync_status"),
    )


class CampaignMessage(Base):
    __tablename__ = "campaign_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        Enum(
            MessageType,
            name="message_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
    )
    channel: Mapped[MessageChannel] = mapped_column(
        Enum(
            MessageChannel,
            name="message_channel",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=MessageChannel.EMAIL,
        server_default=MessageChannel.EMAIL.value,
    )
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    delay_after_previous_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    campaign: Mapped[Campaign] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("campaign_id", "sequence_order", name="uq_campaign_message_order"),
        Index("ix_campaign_messages_campaign_id", "campaign_id"),
        Index("ix_campaign_messages_sequence_order", "campaign_id", "sequence_order"),
    )


class CampaignProspect(Base):
    __tablename__ = "campaign_prospects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"),
        nullable=False,
    )
    instantly_lead_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_status: Mapped[LeadSyncStatus] = mapped_column(
        Enum(
            LeadSyncStatus,
            name="lead_sync_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=LeadSyncStatus.NOT_SYNCED,
        server_default=LeadSyncStatus.NOT_SYNCED.value,
    )
    outreach_status: Mapped[OutreachStatus] = mapped_column(
        Enum(
            OutreachStatus,
            name="outreach_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=OutreachStatus.PENDING,
        server_default=OutreachStatus.PENDING.value,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assessment_status: Mapped[AssessmentStatus] = mapped_column(
        Enum(
            AssessmentStatus,
            name="assessment_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=AssessmentStatus.NOT_STARTED,
        server_default=AssessmentStatus.NOT_STARTED.value,
    )
    cold_status: Mapped[ColdStatus] = mapped_column(
        Enum(
            ColdStatus,
            name="cold_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=ColdStatus.NOT_COLD,
        server_default=ColdStatus.NOT_COLD.value,
    )
    assessment_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assessment_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    proof_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    demo_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    demo_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    demo_watch_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    value_context_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booking_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_booked", server_default="not_booked"
    )
    calcom_booking_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    calcom_booking_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meeting_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meeting_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meeting_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meeting_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meeting_join_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    calendar_unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consultation_show_status: Mapped[ConsultationShowStatus] = mapped_column(
        Enum(
            ConsultationShowStatus,
            name="consultation_show_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=ConsultationShowStatus.NOT_RECORDED,
        server_default=ConsultationShowStatus.NOT_RECORDED.value,
    )
    consultation_show_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purchase_outcome: Mapped[PurchaseOutcome] = mapped_column(
        Enum(
            PurchaseOutcome,
            name="purchase_outcome",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=PurchaseOutcome.NOT_RECORDED,
        server_default=PurchaseOutcome.NOT_RECORDED.value,
    )
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revenue_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    recurring_revenue_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    revenue_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )
    # Jeanne post-consult checkout offer (prospect pays via JeanneCAIO Stripe).
    checkout_offered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkout_offer_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    checkout_offer_recurring_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    checkout_offer_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )
    # Post-purchase Cepoch handoff.
    handoff_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=HandoffStatus.NOT_STARTED.value,
        server_default=HandoffStatus.NOT_STARTED.value,
    )
    handoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stripe Checkout Session id (idempotency for success URL / webhook).
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Attribution snapshots taken at Instantly lead sync (survive later campaign edits).
    outreach_message_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outreach_message_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_signal_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    journey_stage: Mapped[JourneyStage] = mapped_column(
        Enum(
            JourneyStage,
            name="journey_stage",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=JourneyStage.OUTREACH,
        server_default=JourneyStage.OUTREACH.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    campaign: Mapped[Campaign] = relationship(back_populates="campaign_prospects")
    prospect: Mapped[Prospect] = relationship()

    __table_args__ = (
        UniqueConstraint("campaign_id", "prospect_id", name="uq_campaign_prospect"),
        Index("ix_campaign_prospects_campaign_id", "campaign_id"),
        Index("ix_campaign_prospects_prospect_id", "prospect_id"),
        Index("ix_campaign_prospects_instantly_lead_id", "instantly_lead_id"),
        Index("ix_campaign_prospects_sync_status", "sync_status"),
        Index("ix_campaign_prospects_outreach_status", "outreach_status"),
        Index("ix_campaign_prospects_assessment_token", "assessment_token", unique=True),
        Index("ix_campaign_prospects_journey_stage", "journey_stage"),
        Index(
            "ix_campaign_prospects_stripe_checkout_session_id",
            "stripe_checkout_session_id",
            unique=True,
        ),
    )
