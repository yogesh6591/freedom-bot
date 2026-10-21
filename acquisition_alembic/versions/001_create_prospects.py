"""Create prospects table.

Revision ID: 001_create_prospects
Revises:
Create Date: 2026-09-22 10:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_create_prospects"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

approval_status_enum = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    name="approval_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(
        "pending",
        "approved",
        "rejected",
        name="approval_status",
    ).create(bind, checkfirst=True)

    op.create_table(
        "prospects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("linkedin_url", sa.String(length=512), nullable=True),
        sa.Column("executive_name", sa.String(length=255), nullable=False),
        sa.Column("executive_title", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("revenue_band", sa.String(length=100), nullable=True),
        sa.Column("headcount_band", sa.String(length=100), nullable=True),
        sa.Column("fit_note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column(
            "approval_status",
            approval_status_enum,
            server_default="pending",
            nullable=False,
        ),
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
    )
    op.create_index("ix_prospects_email", "prospects", ["email"], unique=False)
    op.create_index("ix_prospects_approval_status", "prospects", ["approval_status"], unique=False)
    op.create_index("ix_prospects_source", "prospects", ["source"], unique=False)
    op.create_index("ix_prospects_company", "prospects", ["company"], unique=False)
    op.create_index("ix_prospects_created_at", "prospects", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_prospects_created_at", table_name="prospects")
    op.drop_index("ix_prospects_company", table_name="prospects")
    op.drop_index("ix_prospects_source", table_name="prospects")
    op.drop_index("ix_prospects_approval_status", table_name="prospects")
    op.drop_index("ix_prospects_email", table_name="prospects")
    op.drop_table("prospects")
    postgresql.ENUM(name="approval_status").drop(op.get_bind(), checkfirst=True)
