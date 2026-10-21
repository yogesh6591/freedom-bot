"""Add persisted personalized Value opportunity per assessment profile.

Revision ID: 014_personalized_value
Revises: 013_journey_content_calcom
Create Date: 2026-09-23 17:55:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014_personalized_value"
down_revision: Union[str, Sequence[str], None] = "013_journey_content_calcom"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessment_profiles",
        sa.Column("personalized_value_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "assessment_profiles",
        sa.Column(
            "personalized_value_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("assessment_profiles", "personalized_value_meta")
    op.drop_column("assessment_profiles", "personalized_value_text")
