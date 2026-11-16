"""Prospect ORM — channel-neutral permanent lead record for autonomous acquisition."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from acquisition.db.base import Base


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AcquisitionStatus(str, enum.Enum):
    """Autonomous acquisition lifecycle (orthogonal to ApprovalStatus / outreach)."""

    DISCOVERED = "discovered"
    RESEARCHED = "researched"
    READY_FOR_SCORING = "ready_for_scoring"
    SCORED = "scored"
    READY_FOR_OUTREACH = "ready_for_outreach"
    NEEDS_MORE_RESEARCH = "needs_more_research"
    UNREACHABLE = "unreachable"
    EXCLUDED = "excluded"


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    #: BizOS workspace that owns this prospect (control-plane ``clients.id``).
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # --- Company (existing + extensions) ---
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    revenue_band: Mapped[str | None] = mapped_column(String(100), nullable=True)
    headcount_band: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locations_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    # --- Decision maker ---
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    executive_name: Mapped[str] = mapped_column(String(255), nullable=False)
    executive_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authority_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_founder_or_equivalent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Contact path ---
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    company_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    other_contact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- Fit / need context (scores populated by ProspectScoringService) ---
    fit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_signals: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_reasons: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    timing_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    timing_reasons: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    reachability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reachability_reasons: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority_reasons: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    # Learning loop: preserve base priority_score and store an additive adjustment.
    learning_adjustment: Mapped[float | None] = mapped_column(Float, nullable=True)
    learning_adjustment_reasons: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    effective_priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    learning_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    learning_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    learning_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    learning_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    learning_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    learning_historical_sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    learning_matched_patterns: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    score_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Discovery identity / provenance (Build 1) ---
    # Registry identifier for NPPES-sourced organizations (durable dedupe key).
    npi: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Which discovery source first found this company (openai_web / legacy labels).
    discovery_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discovery_slice_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    website_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # AI business classification from public research (what the firm actually IS).
    business_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    classification_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # --- Lifecycle ---
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            name="approval_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    acquisition_status: Mapped[AcquisitionStatus] = mapped_column(
        Enum(
            AcquisitionStatus,
            name="acquisition_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=False,
            native_enum=True,
        ),
        nullable=False,
        default=AcquisitionStatus.DISCOVERED,
        server_default=AcquisitionStatus.DISCOVERED.value,
    )
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_researched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Chat-started web research: queued -> running -> done / failed. Queued or running people are on hold for email.
    research_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    research_batch: Mapped[str | None] = mapped_column(String(36), nullable=True)
    research_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # One of the saved ICP category names, or "Other"; and the exact search text that found this prospect.
    icp_category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    discovery_query: Mapped[str | None] = mapped_column(String(600), nullable=True)
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

    research_sources: Mapped[list[ProspectResearchSource]] = relationship(
        back_populates="prospect",
        cascade="all, delete-orphan",
        order_by="ProspectResearchSource.checked_at.desc()",
    )

    __table_args__ = (
        Index("ix_prospects_email", "email"),
        Index("ix_prospects_approval_status", "approval_status"),
        Index("ix_prospects_source", "source"),
        Index("ix_prospects_company", "company"),
        Index("ix_prospects_created_at", "created_at"),
        Index("ix_prospects_domain", "domain"),
        Index("ix_prospects_acquisition_status", "acquisition_status"),
        Index("ix_prospects_priority_score", "priority_score"),
        Index("ix_prospects_npi", "npi"),
        Index("ix_prospects_discovery_source", "discovery_source"),
    )


class ProspectResearchSource(Base):
    """Provenance for researched facts/signals — inference must not become silent fact."""

    __tablename__ = "prospect_research_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prospects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    prospect: Mapped[Prospect] = relationship(back_populates="research_sources")

    __table_args__ = (
        Index("ix_prospect_research_sources_prospect_id", "prospect_id"),
        Index("ix_prospect_research_sources_checked_at", "checked_at"),
    )


class IcpSettings(Base):
    """Singleton configurable ICP rules for autonomous acquisition (V1)."""

    __tablename__ = "icp_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geography: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revenue: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    buyer_titles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    fit_themes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    priority_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    excluded_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    score_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # READY campaign used for autonomous outbound outreach.
    autonomous_campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    activation_batch_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    # Continuous automation controls. Safe defaults remain off until configured.
    # Legacy compatibility field. Runtime automation is controlled directly by
    # discovery_enabled and autonomous_outreach_enabled.
    engine_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    discovery_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    discovery_cycle_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    discovery_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1440, server_default="1440"
    )
    autonomous_outreach_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    activation_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1440, server_default="1440"
    )
    capacity_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="auto", server_default="auto"
    )
    capacity_upcoming_meetings_reduced_at: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    capacity_upcoming_meetings_paused_at: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    capacity_reduced_activation_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    allow_mock_discovery_in_production: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Learning controls and rebuildable metrics cache.
    learning_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    learning_min_sample_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=25, server_default="25"
    )
    learning_max_adjustment: Mapped[float] = mapped_column(
        Float, nullable=False, default=8.0, server_default="8"
    )
    learning_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    learning_recomputed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    learning_profile_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    learning_model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="gpt-4o-mini", server_default="gpt-4o-mini"
    )
    # Enabled discovery sources + per-source fetch_limit per run (legacy multi path).
    discovery_sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Structured discovery coverage memory for the next agentic run.
    discovery_coverage_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AutonomousRunHistory(Base):
    """Concise audit row for scheduled/manual autonomous cycles."""

    __tablename__ = "autonomous_run_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cycle_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    researched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capacity_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_autonomous_run_history_cycle_type_started", "cycle_type", "started_at"),
        Index("ix_autonomous_run_history_status", "status"),
    )


class DiscoverySlice(Base):
    """One searchable slice of the market (source × category × geography × angle).

    The ledger is how discovery avoids repeating itself: each run picks the
    least-recently-run slices, and yield per slice is tracked so exhausted
    slices rest and productive ones come back sooner.
    """

    __tablename__ = "discovery_slices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    angle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Pagination cursor for sources that page (NPPES skip offset).
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    candidates_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    new_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    exhausted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rest_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_discovery_slices_source_last_run", "source", "last_run_at"),
    )


class AcquisitionTraceEvent(Base):
    """One step of a pipeline run, persisted for the Admin Live Trace view."""

    __tablename__ = "acquisition_trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_acquisition_trace_events_run_seq", "run_id", "seq"),
        Index("ix_acquisition_trace_events_created", "created_at"),
    )
