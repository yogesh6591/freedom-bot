"""Acquisition KPI fields: calendar unlock, show/purchase, revenue, attribution.

Revision ID: 028_acquisition_kpi_tracking
Revises: 027_after_booking_video_script
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "028_acquisition_kpi_tracking"
down_revision: Union[str, Sequence[str], None] = "027_after_booking_video_script"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

consultation_show_status = postgresql.ENUM(
    "not_recorded",
    "showed",
    "no_show",
    name="consultation_show_status",
    create_type=False,
)
purchase_outcome = postgresql.ENUM(
    "not_recorded",
    "purchased",
    "did_not_purchase",
    name="purchase_outcome",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    consultation_show_status.create(bind, checkfirst=True)
    purchase_outcome.create(bind, checkfirst=True)

    op.add_column(
        "campaign_prospects",
        sa.Column("calendar_unlocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column(
            "consultation_show_status",
            consultation_show_status,
            nullable=False,
            server_default="not_recorded",
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("consultation_show_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column(
            "purchase_outcome",
            purchase_outcome,
            nullable=False,
            server_default="not_recorded",
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("revenue_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("recurring_revenue_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("revenue_currency", sa.String(length=3), nullable=False, server_default="USD"),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("outreach_message_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("outreach_message_snapshot", sa.Text(), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("outreach_signal_snapshot", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    for col in (
        "outreach_signal_snapshot",
        "outreach_message_snapshot",
        "outreach_message_key",
        "revenue_currency",
        "recurring_revenue_amount",
        "revenue_amount",
        "purchased_at",
        "purchase_outcome",
        "consultation_show_recorded_at",
        "consultation_show_status",
        "calendar_unlocked_at",
    ):
        op.drop_column("campaign_prospects", col)
    purchase_outcome.drop(op.get_bind(), checkfirst=True)
    consultation_show_status.drop(op.get_bind(), checkfirst=True)
