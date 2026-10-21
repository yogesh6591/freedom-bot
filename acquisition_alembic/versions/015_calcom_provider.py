"""Add calcom value to integration_provider enum.

Revision ID: 015_calcom_provider
Revises: 014_personalized_value
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "015_calcom_provider"
down_revision: Union[str, Sequence[str], None] = "014_personalized_value"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'calcom'")


def downgrade() -> None:
    # PostgreSQL cannot easily remove enum values; leave calcom in place.
    pass
