"""Drop SMTP-only after-booking email columns.

Revision ID: 021_drop_after_booking_email
Revises: 020_after_booking_email
Create Date: 2026-09-24

Cal.com owns booking notification emails. FreedomBot keeps the After Booking
page; transactional SMTP delivery is removed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021_drop_after_booking_email"
down_revision: Union[str, Sequence[str], None] = "020_after_booking_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("campaign_prospects", "after_booking_email_error")
    op.drop_column("campaign_prospects", "after_booking_email_status")
    op.drop_column("campaign_prospects", "after_booking_email_sent_at")
    op.drop_column("journey_settings", "after_booking_email_intro")
    op.drop_column("journey_settings", "after_booking_email_subject")
    op.drop_column("journey_settings", "after_booking_email_enabled")


def downgrade() -> None:
    op.add_column(
        "journey_settings",
        sa.Column(
            "after_booking_email_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_email_subject", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_email_intro", sa.Text(), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("after_booking_email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("after_booking_email_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("after_booking_email_error", sa.Text(), nullable=True),
    )
