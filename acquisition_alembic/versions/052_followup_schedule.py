"""Automatic follow-up: the rule (on/off, days, hour, limit) lives on the one outreach_settings row.

``followup_since`` is set when the switch goes from off to on: only people whose last email went out after that
moment are followed up automatically, so turning it on never emails old conversations.

Revision ID: 052_followup_schedule
Revises: 051_prospect_domain_per_client
Create Date: 2026-11-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "052_followup_schedule"
down_revision: Union[str, Sequence[str], None] = "051_prospect_domain_per_client"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("outreach_settings", sa.Column("followup_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("outreach_settings", sa.Column("followup_days", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("outreach_settings", sa.Column("followup_hour", sa.Integer(), nullable=False, server_default="18"))
    op.add_column("outreach_settings", sa.Column("followup_max", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("outreach_settings", sa.Column("followup_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("followup_since", "followup_max", "followup_hour", "followup_days", "followup_enabled"):
        op.drop_column("outreach_settings", name)
