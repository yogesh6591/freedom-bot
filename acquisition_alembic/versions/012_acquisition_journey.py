"""Reintroduce acquisition journey: proof → demo → value → calendar gates.

Revision ID: 012_acquisition_journey
Revises: 011_drop_sales_journey
Create Date: 2026-09-23 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012_acquisition_journey"
down_revision: Union[str, Sequence[str], None] = "011_drop_sales_journey"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_enum_value(bind, enum_name: str, value: str) -> None:
    # ALTER TYPE … ADD VALUE cannot run inside a transaction block on older PG;
    # SQLAlchemy/Alembic autocommit is fine with commit_as_you_go for ADD VALUE.
    bind.execute(sa.text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'"))


def upgrade() -> None:
    bind = op.get_bind()
    for value in (
        "unqualified_exit",
        "pending_review",
        "proof",
        "demo",
    ):
        _add_enum_value(bind, "journey_stage", value)

    op.add_column(
        "campaign_prospects",
        sa.Column("proof_viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("value_context_completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "INSERT INTO journey_settings "
            "(id, demo_enabled, demo_require_watch, value_enabled, calendar_enabled) "
            "VALUES (1, false, true, false, false)"
        )
    )


def downgrade() -> None:
    op.drop_table("journey_settings")
    op.drop_index(
        "ix_assessment_case_studies_enabled_order",
        table_name="assessment_case_studies",
    )
    op.drop_table("assessment_case_studies")
    op.drop_column("campaign_prospects", "value_context_completed_at")
    op.drop_column("campaign_prospects", "demo_completed_at")
    op.drop_column("campaign_prospects", "demo_started_at")
    op.drop_column("campaign_prospects", "proof_viewed_at")
    # Enum values are left in place (Postgres cannot easily drop enum labels).
