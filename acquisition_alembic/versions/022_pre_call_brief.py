"""Add pre-call brief columns to assessment_profiles.

Revision ID: 022_pre_call_brief
Revises: 021_drop_after_booking_email
Create Date: 2026-09-24

Internal Jeanne pre-call one-pager derived from assessment evidence after booking.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "022_pre_call_brief"
down_revision: Union[str, Sequence[str], None] = "021_drop_after_booking_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_profiles",
        sa.Column("pre_call_brief_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "assessment_profiles",
        sa.Column("pre_call_brief_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessment_profiles",
        sa.Column("pre_call_brief_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "assessment_profiles",
        sa.Column("pre_call_brief_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "assessment_profiles",
        sa.Column("pre_call_brief_model", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_profiles", "pre_call_brief_model")
    op.drop_column("assessment_profiles", "pre_call_brief_error")
    op.drop_column("assessment_profiles", "pre_call_brief_status")
    op.drop_column("assessment_profiles", "pre_call_brief_generated_at")
    op.drop_column("assessment_profiles", "pre_call_brief_data")
