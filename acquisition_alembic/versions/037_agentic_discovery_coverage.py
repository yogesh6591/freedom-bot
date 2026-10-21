"""037 — Agentic discovery coverage memory on ICP settings.

Revision ID: 037_agentic_discovery_coverage
Revises: 036_discovery_engine_v2
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "037_agentic_discovery_coverage"
down_revision: Union[str, Sequence[str], None] = "036_discovery_engine_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icp_settings",
        sa.Column("discovery_coverage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("icp_settings", "discovery_coverage_json")
