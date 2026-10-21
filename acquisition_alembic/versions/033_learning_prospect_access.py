"""Learning loop + acquisition-owner prospect access (Batch 5).

Revision ID: 033_learning_prospect_access
Revises: 032_autonomous_automation
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "033_learning_prospect_access"
down_revision: Union[str, Sequence[str], None] = "032_autonomous_automation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icp_settings",
        sa.Column("learning_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "icp_settings",
        sa.Column("learning_min_sample_size", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "learning_max_adjustment",
            sa.Float(),
            nullable=False,
            server_default="8",
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "learning_metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column("learning_recomputed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "prospects",
        sa.Column("learning_adjustment", sa.Float(), nullable=True),
    )
    op.add_column(
        "prospects",
        sa.Column(
            "learning_adjustment_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "prospects",
        sa.Column("effective_priority_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prospects", "effective_priority_score")
    op.drop_column("prospects", "learning_adjustment_reasons")
    op.drop_column("prospects", "learning_adjustment")
    op.drop_column("icp_settings", "learning_recomputed_at")
    op.drop_column("icp_settings", "learning_metrics_json")
    op.drop_column("icp_settings", "learning_max_adjustment")
    op.drop_column("icp_settings", "learning_min_sample_size")
    op.drop_column("icp_settings", "learning_enabled")
