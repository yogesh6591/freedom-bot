"""Drop demo-only static assessment mode columns from campaign_prospects.

Revision ID: 025_drop_demo_static_assessment
Revises: 024_demo_static_assessment
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025_drop_demo_static_assessment"
down_revision: Union[str, Sequence[str], None] = "024_demo_static_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("campaign_prospects", "demo_static_outcome")
    op.drop_column("campaign_prospects", "demo_static_mode")


def downgrade() -> None:
    op.add_column(
        "campaign_prospects",
        sa.Column(
            "demo_static_mode",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("demo_static_outcome", sa.String(length=32), nullable=True),
    )
