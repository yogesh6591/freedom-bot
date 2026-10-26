"""Prospecting rules singleton + version history.

Revision ID: 043_prospecting_rules
Revises: 042_campaign_prospect_overrides
Create Date: 2026-10-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "043_prospecting_rules"
down_revision: Union[str, Sequence[str], None] = "042_campaign_prospect_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prospecting_rules",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "prospecting_rules_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "rules_id",
            sa.Integer(),
            sa.ForeignKey("prospecting_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False, server_default="save"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_prospecting_rules_versions_rules_id",
        "prospecting_rules_versions",
        ["rules_id"],
    )
    op.create_index(
        "ix_prospecting_rules_versions_version",
        "prospecting_rules_versions",
        ["rules_id", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_prospecting_rules_versions_version", table_name="prospecting_rules_versions")
    op.drop_index("ix_prospecting_rules_versions_rules_id", table_name="prospecting_rules_versions")
    op.drop_table("prospecting_rules_versions")
    op.drop_table("prospecting_rules")
