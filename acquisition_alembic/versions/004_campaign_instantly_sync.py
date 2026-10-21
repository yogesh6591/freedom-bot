"""Add campaign delivery schedule and Instantly sync metadata.

Revision ID: 004_campaign_sync
Revises: 003_integration_conn
Create Date: 2026-09-22 12:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_campaign_sync"
down_revision: Union[str, Sequence[str], None] = "003_integration_conn"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sync_status_enum = postgresql.ENUM(
    "not_synced",
    "synced",
    "sync_error",
    name="instantly_sync_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(
        "not_synced",
        "synced",
        "sync_error",
        name="instantly_sync_status",
    ).create(bind, checkfirst=True)

    op.add_column(
        "campaigns",
        sa.Column(
            "send_timezone",
            sa.String(length=64),
            server_default="America/Chicago",
            nullable=False,
        ),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "send_window_start",
            sa.String(length=5),
            server_default="09:00",
            nullable=False,
        ),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "send_window_end",
            sa.String(length=5),
            server_default="17:00",
            nullable=False,
        ),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_monday", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_tuesday", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_wednesday", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_thursday", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_friday", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_saturday", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("send_sunday", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "campaigns",
        sa.Column("instantly_campaign_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "instantly_sync_status",
            sync_status_enum,
            server_default="not_synced",
            nullable=False,
        ),
    )
    op.add_column(
        "campaigns",
        sa.Column("instantly_last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("instantly_sync_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_campaigns_instantly_campaign_id",
        "campaigns",
        ["instantly_campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaigns_instantly_sync_status",
        "campaigns",
        ["instantly_sync_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_campaigns_instantly_sync_status", table_name="campaigns")
    op.drop_index("ix_campaigns_instantly_campaign_id", table_name="campaigns")
    op.drop_column("campaigns", "instantly_sync_error")
    op.drop_column("campaigns", "instantly_last_synced_at")
    op.drop_column("campaigns", "instantly_sync_status")
    op.drop_column("campaigns", "instantly_campaign_id")
    op.drop_column("campaigns", "send_sunday")
    op.drop_column("campaigns", "send_saturday")
    op.drop_column("campaigns", "send_friday")
    op.drop_column("campaigns", "send_thursday")
    op.drop_column("campaigns", "send_wednesday")
    op.drop_column("campaigns", "send_tuesday")
    op.drop_column("campaigns", "send_monday")
    op.drop_column("campaigns", "send_window_end")
    op.drop_column("campaigns", "send_window_start")
    op.drop_column("campaigns", "send_timezone")
    postgresql.ENUM(name="instantly_sync_status").drop(op.get_bind(), checkfirst=True)
