"""Outreach sending settings (one row): which Gmail sends, footer, on/off.

Revision ID: 045_outreach_settings
Revises: 044_outreach_log
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_outreach_settings"
down_revision: Union[str, Sequence[str], None] = "044_outreach_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outreach_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("sender_user_id", sa.String(length=128), nullable=True),
        sa.Column("business_address", sa.Text(), nullable=True),
        sa.Column("optout_line", sa.Text(), nullable=True),
        sa.Column("worker_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("outreach_settings")
