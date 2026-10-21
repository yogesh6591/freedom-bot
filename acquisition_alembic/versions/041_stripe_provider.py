"""Add stripe value to integration_provider enum.

Revision ID: 041_stripe_provider
Revises: 040_jeanne_checkout_offer
Create Date: 2026-10-12
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "041_stripe_provider"
down_revision: Union[str, Sequence[str], None] = "040_jeanne_checkout_offer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'stripe'")


def downgrade() -> None:
    # PostgreSQL cannot easily remove enum values; leave stripe in place.
    pass
