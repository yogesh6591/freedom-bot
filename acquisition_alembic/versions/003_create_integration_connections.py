"""Create integration_connections table.

Revision ID: 003_integration_conn
Revises: 002_create_campaigns
Create Date: 2026-09-22 12:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_integration_conn"
down_revision: Union[str, Sequence[str], None] = "002_create_campaigns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

provider_enum = postgresql.ENUM(
    "instantly",
    name="integration_provider",
    create_type=False,
)
status_enum = postgresql.ENUM(
    "unknown",
    "connected",
    "error",
    name="connection_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("instantly", name="integration_provider").create(bind, checkfirst=True)
    postgresql.ENUM("unknown", "connected", "error", name="connection_status").create(
        bind, checkfirst=True
    )

    op.create_table(
        "integration_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column(
            "connection_status",
            status_enum,
            server_default="unknown",
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", name="uq_integration_connections_provider"),
    )
    op.create_index(
        "ix_integration_connections_provider",
        "integration_connections",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_integration_connections_status",
        "integration_connections",
        ["connection_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_integration_connections_status", table_name="integration_connections")
    op.drop_index("ix_integration_connections_provider", table_name="integration_connections")
    op.drop_table("integration_connections")
    postgresql.ENUM(name="connection_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="integration_provider").drop(op.get_bind(), checkfirst=True)
