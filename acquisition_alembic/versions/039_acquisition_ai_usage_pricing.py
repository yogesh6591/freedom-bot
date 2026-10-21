"""Acquisition AI usage — nullable costs + pricing completeness.

Revision ID: 039_acquisition_ai_usage_pricing
Revises: 038_acquisition_ai_usage
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "039_acquisition_ai_usage_pricing"
down_revision: Union[str, Sequence[str], None] = "038_acquisition_ai_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "acquisition_ai_usage",
        sa.Column("pricing_status", sa.String(length=64), nullable=False, server_default="ok"),
    )
    op.add_column(
        "acquisition_ai_usage",
        sa.Column("cost_complete", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.alter_column("acquisition_ai_usage", "input_cost_usd", existing_type=sa.Float(), nullable=True)
    op.alter_column("acquisition_ai_usage", "output_cost_usd", existing_type=sa.Float(), nullable=True)
    op.alter_column("acquisition_ai_usage", "web_search_cost_usd", existing_type=sa.Float(), nullable=True)
    op.alter_column("acquisition_ai_usage", "total_cost_usd", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE acquisition_ai_usage SET total_cost_usd = 0 WHERE total_cost_usd IS NULL")
    op.execute("UPDATE acquisition_ai_usage SET input_cost_usd = 0 WHERE input_cost_usd IS NULL")
    op.execute("UPDATE acquisition_ai_usage SET output_cost_usd = 0 WHERE output_cost_usd IS NULL")
    op.execute("UPDATE acquisition_ai_usage SET web_search_cost_usd = 0 WHERE web_search_cost_usd IS NULL")
    op.alter_column("acquisition_ai_usage", "total_cost_usd", existing_type=sa.Float(), nullable=False)
    op.alter_column("acquisition_ai_usage", "web_search_cost_usd", existing_type=sa.Float(), nullable=False)
    op.alter_column("acquisition_ai_usage", "output_cost_usd", existing_type=sa.Float(), nullable=False)
    op.alter_column("acquisition_ai_usage", "input_cost_usd", existing_type=sa.Float(), nullable=False)
    op.drop_column("acquisition_ai_usage", "cost_complete")
    op.drop_column("acquisition_ai_usage", "pricing_status")
