"""After-booking journey: booking fields, welcome video, YouTube links.

Revision ID: 018_after_booking
Revises: 017_demo_watch
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018_after_booking"
down_revision: Union[str, Sequence[str], None] = "017_demo_watch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # journey_stage value 'booked' already exists from earlier migrations.
    op.add_column(
        "campaign_prospects",
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column(
            "booking_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_booked",
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("calcom_booking_uid", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("calcom_booking_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("meeting_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("meeting_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("meeting_timezone", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("meeting_location", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("meeting_join_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("after_booking_email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_campaign_prospects_calcom_booking_uid",
        "campaign_prospects",
        ["calcom_booking_uid"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_prospects_booking_status",
        "campaign_prospects",
        ["booking_status"],
        unique=False,
    )

    op.add_column(
        "journey_settings",
        sa.Column(
            "after_booking_video_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_video_heading", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_video_intro", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_video_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column(
            "after_booking_video_is_test",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_prep_heading", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_prep_body", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_prep_note", sa.Text(), nullable=True),
    )

    op.create_table(
        "after_booking_youtube_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("youtube_url", sa.String(length=1024), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "content_status",
            sa.String(length=32),
            nullable=False,
            server_default="test",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_after_booking_youtube_enabled_order",
        "after_booking_youtube_links",
        ["enabled", "display_order"],
    )

    # Seed sensible working defaults (TEST welcome video reuses packaged demo asset).
    op.execute(
        sa.text(
            """
            UPDATE journey_settings SET
              after_booking_video_heading = COALESCE(after_booking_video_heading, 'A quick welcome before your meeting'),
              after_booking_video_intro = COALESCE(
                after_booking_video_intro,
                'Watch this short welcome from Jeanne before your conversation.'
              ),
              after_booking_video_url = COALESCE(
                after_booking_video_url,
                '/static/demo/FreedomBot_Executive_Demo_v1.mp4'
              ),
              after_booking_prep_heading = COALESCE(
                after_booking_prep_heading,
                'Prepare for your conversation with Jeanne'
              ),
              after_booking_prep_body = COALESCE(
                after_booking_prep_body,
                'Jeanne will have the context from your assessment, so you will not need to restart basic discovery. Come prepared with specific questions about your business, including anything you want to ask such as ''Can FreedomBot do this?'''
              ),
              after_booking_prep_note = COALESCE(
                after_booking_prep_note,
                'Optional: browse a few of Jeanne''s videos below if you would like to get to know her before the meeting.'
              )
            WHERE id = 1
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_after_booking_youtube_enabled_order", table_name="after_booking_youtube_links")
    op.drop_table("after_booking_youtube_links")
    for col in (
        "after_booking_prep_note",
        "after_booking_prep_body",
        "after_booking_prep_heading",
        "after_booking_video_is_test",
        "after_booking_video_url",
        "after_booking_video_intro",
        "after_booking_video_heading",
        "after_booking_video_enabled",
    ):
        op.drop_column("journey_settings", col)
    op.drop_index("ix_campaign_prospects_booking_status", table_name="campaign_prospects")
    op.drop_index("ix_campaign_prospects_calcom_booking_uid", table_name="campaign_prospects")
    for col in (
        "after_booking_email_sent_at",
        "meeting_join_url",
        "meeting_location",
        "meeting_timezone",
        "meeting_end",
        "meeting_start",
        "calcom_booking_id",
        "calcom_booking_uid",
        "booking_status",
        "booked_at",
    ):
        op.drop_column("campaign_prospects", col)
