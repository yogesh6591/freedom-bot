"""Per-lead name/company overrides typed by the user in the email card.

Revision ID: 042_campaign_prospect_overrides
Revises: 041_stripe_provider
Create Date: 2026-10-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "042_campaign_prospect_overrides"
down_revision: Union[str, Sequence[str], None] = "041_stripe_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("campaign_prospects", sa.Column("first_name_override", sa.String(length=255), nullable=True))
    op.add_column("campaign_prospects", sa.Column("last_name_override", sa.String(length=255), nullable=True))
    op.add_column("campaign_prospects", sa.Column("company_override", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("campaign_prospects", "company_override")
    op.drop_column("campaign_prospects", "last_name_override")
    op.drop_column("campaign_prospects", "first_name_override")
