"""Outreach stage templates: one saved email body per journey stage (JSON).

Revision ID: 047_stage_templates
Revises: 046_outreach_templates
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047_stage_templates"
down_revision: Union[str, Sequence[str], None] = "046_outreach_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("outreach_settings", sa.Column("stage_templates", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("outreach_settings", "stage_templates")
