"""Add openai value to integration_provider enum.

Revision ID: 006_openai_provider
Revises: 005_outbound_flow
Create Date: 2026-09-22 17:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "006_openai_provider"
down_revision: Union[str, Sequence[str], None] = "005_outbound_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'openai'")


def downgrade() -> None:
    # PostgreSQL cannot easily drop enum values; leave as no-op.
    pass
