"""035 — AI outcome learning model config + richer audit fields.

Revision ID: 035_ai_outcome_learning_audit
Revises: 034_ai_outcome_learning
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "035_ai_outcome_learning_audit"
down_revision: Union[str, Sequence[str], None] = "034_ai_outcome_learning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icp_settings",
        sa.Column(
            "learning_model",
            sa.String(length=64),
            nullable=False,
            server_default="gpt-4o-mini",
        ),
    )
    op.add_column(
        "prospects",
        sa.Column("learning_prompt_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "prospects",
        sa.Column("learning_historical_sample_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "prospects",
        sa.Column(
            "learning_matched_patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("prospects", "learning_matched_patterns")
    op.drop_column("prospects", "learning_historical_sample_size")
    op.drop_column("prospects", "learning_prompt_version")
    op.drop_column("icp_settings", "learning_model")
