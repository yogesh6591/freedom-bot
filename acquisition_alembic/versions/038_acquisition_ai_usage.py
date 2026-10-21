"""Acquisition AI usage table.

Revision ID: 038_acquisition_ai_usage
Revises: 037_agentic_discovery_coverage
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "038_acquisition_ai_usage"
down_revision: Union[str, Sequence[str], None] = "037_agentic_discovery_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "acquisition_ai_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_id", sa.String(length=128), nullable=True),
        sa.Column("call_type", sa.String(length=64), nullable=False),
        sa.Column("slot_id", sa.String(length=64), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("web_search_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("web_search_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ok"),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_acquisition_ai_usage_run_id", "acquisition_ai_usage", ["run_id"])
    op.create_index("ix_acquisition_ai_usage_call_type", "acquisition_ai_usage", ["call_type"])
    op.create_index("ix_acquisition_ai_usage_created", "acquisition_ai_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_acquisition_ai_usage_created", table_name="acquisition_ai_usage")
    op.drop_index("ix_acquisition_ai_usage_call_type", table_name="acquisition_ai_usage")
    op.drop_index("ix_acquisition_ai_usage_run_id", table_name="acquisition_ai_usage")
    op.drop_table("acquisition_ai_usage")
