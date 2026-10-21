"""Acquisition journey settings and approved case-study content."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acquisition.db.base import Base


class CaseStudyContentStatus(str, enum.Enum):
    TEST = "test"
    APPROVED = "approved"


# Recommended Jeanne welcome/prep video script (Admin only — not shown publicly).
DEFAULT_AFTER_BOOKING_VIDEO_SCRIPT = """
Hi, I’m Jeanne.

Thank you for completing the FreedomBot assessment and booking time with me.

By the time we meet, I’ll already have the business context you shared through the assessment, including the areas where there may be inefficiencies, delays, unnecessary dependency on key people, or opportunities to create more leverage.

That means we won’t need to start the conversation from zero or repeat all of the basic discovery.

Our meeting is really about your specific business — what is happening today, what may be worth changing, where FreedomBot may or may not fit, and what the right next step could look like.

Before we meet, think about the areas in your company where you find yourself asking:

“Can FreedomBot do this?”

That might be a workflow, a handoff, an approval process, internal knowledge, repetitive work, reporting, follow-up, or another area where the business depends too heavily on people or fragmented systems.

Bring those questions with you.

We’ll use your assessment as the starting point and focus the conversation on what makes sense for your company.

I’m looking forward to speaking with you.
""".strip()


class JourneySettings(Base):
    """Singleton (id=1) admin-configurable demo / value / calendar content."""

    __tablename__ = "journey_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Demo
    demo_video_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    demo_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    demo_require_watch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    demo_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    demo_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_is_test_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Value / price (no Stripe; budget readiness captured here)
    value_context_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    value_heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_investment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_supporting_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_label_ready: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget_label_unclear: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget_label_not_ready: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Ordered readiness choices shown on Value (1–4). Each: {outcome, label}.
    budget_options: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    value_continue_button_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    value_is_test_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Calendar — non-secret Cal.com identifiers only (API key stays in env)
    calendar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    calendar_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    calcom_event_type_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calcom_event_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calcom_event_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calcom_event_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calcom_booking_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    calcom_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # After booking — welcome video + meeting prep (YouTube links in separate table)
    after_booking_video_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    after_booking_video_heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    after_booking_video_intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_booking_video_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    after_booking_video_poster_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    after_booking_video_is_test: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Recommended recording script for Jeanne (Admin editable; never public).
    after_booking_video_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_booking_prep_heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    after_booking_prep_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_booking_prep_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AfterBookingYoutubeLink(Base):
    """Optional 'Get to know Jeanne' YouTube links shown after booking."""

    __tablename__ = "after_booking_youtube_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    youtube_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CaseStudyContentStatus.TEST.value
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


# Keep AssessmentCaseStudy below JourneySettings for readability in this module.


class AssessmentCaseStudy(Base):
    """Admin-managed proof content. Never invent client results in UI."""

    __tablename__ = "assessment_case_studies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    client_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    problem: Mapped[str] = mapped_column(Text, nullable=False, default="")
    change: Mapped[str] = mapped_column(Text, nullable=False, default="")
    measurable_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CaseStudyContentStatus.TEST.value
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
