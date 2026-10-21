"""Extend acquisition content fields + Cal.com non-secret config.

Revision ID: 013_journey_content_calcom
Revises: 012_acquisition_journey
Create Date: 2026-09-23 16:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013_journey_content_calcom"
down_revision: Union[str, Sequence[str], None] = "012_acquisition_journey"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_case_studies",
        sa.Column("client_display_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "assessment_case_studies",
        sa.Column(
            "content_status",
            sa.String(length=32),
            nullable=False,
            server_default="test",
        ),
    )

    op.add_column(
        "journey_settings",
        sa.Column("demo_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("demo_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column(
            "demo_is_test_content",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "journey_settings",
        sa.Column("value_heading", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("value_intro", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("value_investment_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("value_supporting_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("budget_question", sa.Text(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("budget_label_ready", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("budget_label_unclear", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("budget_label_not_ready", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("value_continue_button_text", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column(
            "value_is_test_content",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "journey_settings",
        sa.Column("calcom_event_type_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("calcom_event_slug", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("calcom_event_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("calcom_event_duration_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("calcom_booking_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("calcom_username", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    for col in (
        "calcom_username",
        "calcom_booking_url",
        "calcom_event_duration_minutes",
        "calcom_event_title",
        "calcom_event_slug",
        "calcom_event_type_id",
        "value_is_test_content",
        "value_continue_button_text",
        "budget_label_not_ready",
        "budget_label_unclear",
        "budget_label_ready",
        "budget_question",
        "value_supporting_note",
        "value_investment_text",
        "value_intro",
        "value_heading",
        "demo_is_test_content",
        "demo_description",
        "demo_title",
    ):
        op.drop_column("journey_settings", col)
    op.drop_column("assessment_case_studies", "content_status")
    op.drop_column("assessment_case_studies", "client_display_name")
