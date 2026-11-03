"""Outreach template sending: toggle plus first-email and follow-up templates.

Revision ID: 046_outreach_templates
Revises: 045_outreach_settings
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "046_outreach_templates"
down_revision: Union[str, Sequence[str], None] = "045_outreach_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outreach_settings",
        sa.Column("auto_send_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("outreach_settings", sa.Column("first_subject", sa.Text(), nullable=True))
    op.add_column("outreach_settings", sa.Column("first_body", sa.Text(), nullable=True))
    op.add_column("outreach_settings", sa.Column("followup_body", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("outreach_settings", "followup_body")
    op.drop_column("outreach_settings", "first_body")
    op.drop_column("outreach_settings", "first_subject")
    op.drop_column("outreach_settings", "auto_send_enabled")
