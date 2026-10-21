"""034 — AI-assisted outcome learning audit fields + profile version.

Revision ID: 034_ai_outcome_learning
Revises: 033_learning_prospect_access
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "034_ai_outcome_learning"
down_revision: Union[str, Sequence[str], None] = "033_learning_prospect_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icp_settings",
        sa.Column(
            "learning_profile_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Production default sample threshold for AI outcome learning.
    op.alter_column(
        "icp_settings",
        "learning_min_sample_size",
        existing_type=sa.Integer(),
        server_default="25",
    )
    op.execute(
        sa.text(
            "UPDATE icp_settings SET learning_min_sample_size = 25 "
            "WHERE learning_min_sample_size = 5"
        )
    )

    op.add_column(
        "prospects",
        sa.Column("learning_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "prospects",
        sa.Column("learning_model", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "prospects",
        sa.Column("learning_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "prospects",
        sa.Column("learning_profile_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prospects", "learning_profile_version")
    op.drop_column("prospects", "learning_evaluated_at")
    op.drop_column("prospects", "learning_model")
    op.drop_column("prospects", "learning_confidence")
    op.alter_column(
        "icp_settings",
        "learning_min_sample_size",
        existing_type=sa.Integer(),
        server_default="5",
    )
    op.drop_column("icp_settings", "learning_profile_version")
