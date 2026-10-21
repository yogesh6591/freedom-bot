"""Drop premature post-assessment sales-journey schema only.

Keeps assessment lifecycle columns: journey_stage, qualification_status,
assessment timestamps, structured assessment data, and result payloads.

Revision ID: 011_drop_sales_journey
Revises: 010_assessment_journey
Create Date: 2026-09-23 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011_drop_sales_journey"
down_revision: Union[str, Sequence[str], None] = "010_assessment_journey"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_assessment_case_studies_enabled_order",
        table_name="assessment_case_studies",
    )
    op.drop_table("assessment_case_studies")
    op.drop_table("journey_settings")
    op.drop_column("campaign_prospects", "demo_completed_at")
    op.drop_column("campaign_prospects", "demo_started_at")
    # journey_stage enum values for case_studies/demo/value/calendar/booked remain
    # in Postgres (unused). Altering native enums is risky; app no longer writes them.


def downgrade() -> None:
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "INSERT INTO journey_settings "
            "(id, demo_enabled, demo_require_watch, value_enabled, calendar_enabled) "
            "VALUES (1, false, true, false, false)"
        )
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
