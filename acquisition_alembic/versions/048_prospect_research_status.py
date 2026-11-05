"""Prospect research status: queued / running / done / failed, grouped by batch.

Revision ID: 048_prospect_research_status
Revises: 047_stage_templates
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "048_prospect_research_status"
down_revision: Union[str, Sequence[str], None] = "047_stage_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("research_status", sa.String(16), nullable=True))
    op.add_column("prospects", sa.Column("research_batch", sa.String(36), nullable=True))
    op.add_column("prospects", sa.Column("research_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_prospects_research_status", "prospects", ["research_status"])


def downgrade() -> None:
    op.drop_index("ix_prospects_research_status", table_name="prospects")
    op.drop_column("prospects", "research_updated_at")
    op.drop_column("prospects", "research_batch")
    op.drop_column("prospects", "research_status")
