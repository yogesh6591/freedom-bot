"""Extend assessment journey: stages, qualification status, case studies, journey settings.

Revision ID: 010_assessment_journey
Revises: 009_assessment_conversation
Create Date: 2026-09-23 11:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010_assessment_journey"
down_revision: Union[str, Sequence[str], None] = "009_assessment_conversation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    journey = postgresql.ENUM(
        "outreach",
        "assessment_started",
        "assessment_completed",
        "qualified",
        "unqualified",
        "case_studies",
        "demo_started",
        "demo_completed",
        "value",
        "calendar_unlocked",
        "booked",
        name="journey_stage",
        create_type=False,
    )
    journey.create(op.get_bind(), checkfirst=True)

    qual = postgresql.ENUM(
        "pending",
        "qualified",
        "unqualified",
        name="qualification_status",
        create_type=False,
    )
    qual.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "campaign_prospects",
        sa.Column(
            "journey_stage",
            postgresql.ENUM(
                "outreach",
                "assessment_started",
                "assessment_completed",
                "qualified",
                "unqualified",
                "case_studies",
                "demo_started",
                "demo_completed",
                "value",
                "calendar_unlocked",
                "booked",
                name="journey_stage",
                create_type=False,
            ),
            server_default="outreach",
            nullable=False,
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_campaign_prospects_journey_stage", "campaign_prospects", ["journey_stage"])

    op.add_column(
        "assessment_profiles",
        sa.Column(
            "qualification_status",
            postgresql.ENUM(
                "pending",
                "qualified",
                "unqualified",
                name="qualification_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_assessment_profiles_qualification_status",
        "assessment_profiles",
        ["qualification_status"],
    )

    op.create_table(
        "assessment_case_studies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False, server_default=""),
        sa.Column("change", sa.Text(), nullable=False, server_default=""),
        sa.Column("measurable_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
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
        "ix_assessment_case_studies_enabled_order",
        "assessment_case_studies",
        ["enabled", "display_order"],
    )

    op.create_table(
        "journey_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("demo_video_url", sa.String(length=1024), nullable=True),
        sa.Column("demo_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("demo_require_watch", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("value_context_html", sa.Text(), nullable=True),
        sa.Column("value_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("calendar_url", sa.String(length=1024), nullable=True),
        sa.Column("calendar_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO journey_settings (id, demo_enabled, demo_require_watch, value_enabled, calendar_enabled) "
            "VALUES (1, false, true, false, false)"
        )
    )


def downgrade() -> None:
    op.drop_table("journey_settings")
    op.drop_index("ix_assessment_case_studies_enabled_order", table_name="assessment_case_studies")
    op.drop_table("assessment_case_studies")
    op.drop_index("ix_assessment_profiles_qualification_status", table_name="assessment_profiles")
    op.drop_column("assessment_profiles", "qualification_status")
    op.drop_index("ix_campaign_prospects_journey_stage", table_name="campaign_prospects")
    op.drop_column("campaign_prospects", "demo_completed_at")
    op.drop_column("campaign_prospects", "demo_started_at")
    op.drop_column("campaign_prospects", "journey_stage")
    op.execute("DROP TYPE IF EXISTS qualification_status")
    op.execute("DROP TYPE IF EXISTS journey_stage")
