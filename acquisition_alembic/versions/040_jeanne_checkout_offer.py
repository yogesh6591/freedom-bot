"""Jeanne post-consult checkout offer + Cepoch handoff fields.

Revision ID: 040_jeanne_checkout_offer
Revises: 039_acquisition_ai_usage_pricing
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "040_jeanne_checkout_offer"
down_revision: Union[str, Sequence[str], None] = "039_acquisition_ai_usage_pricing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaign_prospects",
        sa.Column("checkout_offered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("checkout_offer_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("checkout_offer_recurring_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column(
            "checkout_offer_currency",
            sa.String(length=3),
            nullable=False,
            server_default="USD",
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column(
            "handoff_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_started",
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("handoff_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_prospects", "handoff_at")
    op.drop_column("campaign_prospects", "handoff_status")
    op.drop_column("campaign_prospects", "checkout_offer_currency")
    op.drop_column("campaign_prospects", "checkout_offer_recurring_amount")
    op.drop_column("campaign_prospects", "checkout_offer_amount")
    op.drop_column("campaign_prospects", "checkout_offered_at")
