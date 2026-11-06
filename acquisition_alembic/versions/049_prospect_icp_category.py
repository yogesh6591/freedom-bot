"""Prospect ICP category (one of the saved categories or Other) and the search that found the prospect.

Revision ID: 049_prospect_icp_category
Revises: 048_prospect_research_status
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "049_prospect_icp_category"
down_revision: Union[str, Sequence[str], None] = "048_prospect_research_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("icp_category", sa.String(128), nullable=True))
    op.add_column("prospects", sa.Column("discovery_query", sa.String(600), nullable=True))
    op.create_index("ix_prospects_icp_category", "prospects", ["icp_category"])


def downgrade() -> None:
    op.drop_index("ix_prospects_icp_category", table_name="prospects")
    op.drop_column("prospects", "discovery_query")
    op.drop_column("prospects", "icp_category")
