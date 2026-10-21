"""Drop cold-lead outreach_settings (Outreach admin tab removed).

Revision ID: 008_drop_outreach_settings
Revises: 007_ai_system_prompt
Create Date: 2026-09-22 18:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_drop_outreach_settings"
down_revision: Union[str, Sequence[str], None] = "007_ai_system_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("outreach_settings")


def downgrade() -> None:
    op.create_table(
        "outreach_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cold_logic_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("cold_after_days_without_activity", sa.Integer(), server_default="14", nullable=False),
        sa.Column("cold_after_completed_sequence", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO outreach_settings "
            "(id, cold_logic_enabled, cold_after_days_without_activity, cold_after_completed_sequence) "
            "VALUES (1, true, 14, true)"
        )
    )
