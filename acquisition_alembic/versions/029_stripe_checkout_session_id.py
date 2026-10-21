"""Add nullable stripe_checkout_session_id for TEMPORARY Stripe TEST idempotency.

Revision ID: 029_stripe_checkout_session_id
Revises: 028_acquisition_kpi_tracking
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029_stripe_checkout_session_id"
down_revision: Union[str, Sequence[str], None] = "028_acquisition_kpi_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaign_prospects",
        sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_campaign_prospects_stripe_checkout_session_id",
        "campaign_prospects",
        ["stripe_checkout_session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_campaign_prospects_stripe_checkout_session_id",
        table_name="campaign_prospects",
    )
    op.drop_column("campaign_prospects", "stripe_checkout_session_id")
