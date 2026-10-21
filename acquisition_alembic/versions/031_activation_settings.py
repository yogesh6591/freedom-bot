"""Autonomous activation settings migration (campaign pointer + batch limit).

Revision ID: 031_activation_settings
Revises: 030_acquisition_foundation
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "031_activation_settings"
down_revision: Union[str, Sequence[str], None] = "030_acquisition_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icp_settings",
        sa.Column("autonomous_campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "activation_batch_limit",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.create_foreign_key(
        "fk_icp_settings_autonomous_campaign_id",
        "icp_settings",
        "campaigns",
        ["autonomous_campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_icp_settings_autonomous_campaign_id", "icp_settings", type_="foreignkey")
    op.drop_column("icp_settings", "activation_batch_limit")
    op.drop_column("icp_settings", "autonomous_campaign_id")
