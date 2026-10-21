"""Add budget_options JSONB on journey_settings.

Revision ID: 016_budget_options
Revises: 015_calcom_provider
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016_budget_options"
down_revision: Union[str, Sequence[str], None] = "015_calcom_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "journey_settings",
        sa.Column(
            "budget_options",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("journey_settings", "budget_options")
