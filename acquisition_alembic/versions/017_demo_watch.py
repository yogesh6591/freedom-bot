"""Add demo_watch_completed_at for require-full-watch gating.

Revision ID: 017_demo_watch
Revises: 016_budget_options
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017_demo_watch"
down_revision: Union[str, Sequence[str], None] = "016_budget_options"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_watch_completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_prospects", "demo_watch_completed_at")
