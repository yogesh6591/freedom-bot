"""Prospects scoped per FreedomBot workspace (client_id).

Revision ID: 050_prospect_client_id
Revises: 049_prospect_icp_category
Create Date: 2026-11-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "050_prospect_client_id"
down_revision: Union[str, Sequence[str], None] = "049_prospect_icp_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("client_id", sa.String(64), nullable=True))
    op.create_index("ix_prospects_client_id", "prospects", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_prospects_client_id", table_name="prospects")
    op.drop_column("prospects", "client_id")