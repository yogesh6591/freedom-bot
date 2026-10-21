"""Create campaigns, campaign_messages, and campaign_prospects.

Revision ID: 002_create_campaigns
Revises: 001_create_prospects
Create Date: 2026-09-22 11:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_create_campaigns"
down_revision: Union[str, Sequence[str], None] = "001_create_prospects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

campaign_status_enum = postgresql.ENUM(
    "draft",
    "ready",
    "archived",
    name="campaign_status",
    create_type=False,
)
message_type_enum = postgresql.ENUM(
    "initial",
    "follow_up",
    name="message_type",
    create_type=False,
)
message_channel_enum = postgresql.ENUM(
    "email",
    name="message_channel",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("draft", "ready", "archived", name="campaign_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM("initial", "follow_up", name="message_type").create(bind, checkfirst=True)
    postgresql.ENUM("email", name="message_channel").create(bind, checkfirst=True)

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=False),
        sa.Column("assessment_url", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            campaign_status_enum,
            server_default="draft",
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
    op.create_index("ix_campaigns_status", "campaigns", ["status"], unique=False)
    op.create_index("ix_campaigns_created_at", "campaigns", ["created_at"], unique=False)
    op.create_index("ix_campaigns_name", "campaigns", ["name"], unique=False)

    op.create_table(
        "campaign_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.Column("message_type", message_type_enum, nullable=False),
        sa.Column(
            "channel",
            message_channel_enum,
            server_default="email",
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "delay_after_previous_hours",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "sequence_order", name="uq_campaign_message_order"),
    )
    op.create_index(
        "ix_campaign_messages_campaign_id",
        "campaign_messages",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_messages_sequence_order",
        "campaign_messages",
        ["campaign_id", "sequence_order"],
        unique=False,
    )

    op.create_table(
        "campaign_prospects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("prospect_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "prospect_id", name="uq_campaign_prospect"),
    )
    op.create_index(
        "ix_campaign_prospects_campaign_id",
        "campaign_prospects",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_campaign_prospects_prospect_id",
        "campaign_prospects",
        ["prospect_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_prospects_prospect_id", table_name="campaign_prospects")
    op.drop_index("ix_campaign_prospects_campaign_id", table_name="campaign_prospects")
    op.drop_table("campaign_prospects")

    op.drop_index("ix_campaign_messages_sequence_order", table_name="campaign_messages")
    op.drop_index("ix_campaign_messages_campaign_id", table_name="campaign_messages")
    op.drop_table("campaign_messages")

    op.drop_index("ix_campaigns_name", table_name="campaigns")
    op.drop_index("ix_campaigns_created_at", table_name="campaigns")
    op.drop_index("ix_campaigns_status", table_name="campaigns")
    op.drop_table("campaigns")

    postgresql.ENUM(name="message_channel").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="message_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="campaign_status").drop(op.get_bind(), checkfirst=True)
