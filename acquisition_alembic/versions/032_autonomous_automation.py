"""Autonomous continuous automation settings + run history.

Revision ID: 032_autonomous_automation
Revises: 031_activation_settings
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "032_autonomous_automation"
down_revision: Union[str, Sequence[str], None] = "031_activation_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "icp_settings",
        sa.Column("engine_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "icp_settings",
        sa.Column("discovery_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "icp_settings",
        sa.Column("discovery_cycle_limit", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "discovery_interval_minutes", sa.Integer(), nullable=False, server_default="1440"
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "autonomous_outreach_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "activation_interval_minutes", sa.Integer(), nullable=False, server_default="1440"
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column("capacity_mode", sa.String(length=32), nullable=False, server_default="auto"),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "capacity_upcoming_meetings_reduced_at",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "capacity_upcoming_meetings_paused_at",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "capacity_reduced_activation_limit",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )
    op.add_column(
        "icp_settings",
        sa.Column(
            "allow_mock_discovery_in_production",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    op.create_table(
        "autonomous_run_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("cycle_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("researched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("capacity_state", sa.String(length=32), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_autonomous_run_history_cycle_type_started",
        "autonomous_run_history",
        ["cycle_type", "started_at"],
    )
    op.create_index(
        "ix_autonomous_run_history_status",
        "autonomous_run_history",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_autonomous_run_history_status", table_name="autonomous_run_history")
    op.drop_index(
        "ix_autonomous_run_history_cycle_type_started", table_name="autonomous_run_history"
    )
    op.drop_table("autonomous_run_history")
    for col in (
        "allow_mock_discovery_in_production",
        "capacity_reduced_activation_limit",
        "capacity_upcoming_meetings_paused_at",
        "capacity_upcoming_meetings_reduced_at",
        "capacity_mode",
        "activation_interval_minutes",
        "autonomous_outreach_enabled",
        "discovery_interval_minutes",
        "discovery_cycle_limit",
        "discovery_enabled",
        "engine_enabled",
    ):
        op.drop_column("icp_settings", col)
