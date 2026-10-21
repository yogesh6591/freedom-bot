"""Assessment conversation tables, completed_at, and assessment system prompt.

Revision ID: 009_assessment_conversation
Revises: 008_drop_outreach_settings
Create Date: 2026-09-23 11:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_assessment_conversation"
down_revision: Union[str, Sequence[str], None] = "008_drop_outreach_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_ASSESSMENT_PROMPT = "Assessment system prompt has not been configured."


def upgrade() -> None:
    op.add_column(
        "ai_reply_settings",
        sa.Column(
            "assessment_system_prompt",
            sa.Text(),
            nullable=False,
            server_default=DEFAULT_ASSESSMENT_PROMPT,
        ),
    )
    op.alter_column("ai_reply_settings", "assessment_system_prompt", server_default=None)

    op.add_column(
        "campaign_prospects",
        sa.Column("assessment_completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    role = postgresql.ENUM(
        "user",
        "assistant",
        name="assessment_message_role",
        create_type=False,
    )
    role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "assessment_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_prospect_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["campaign_prospect_id"],
            ["campaign_prospects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_prospect_id"),
    )
    op.create_index(
        "ix_assessment_conversations_campaign_prospect_id",
        "assessment_conversations",
        ["campaign_prospect_id"],
    )

    op.create_table(
        "assessment_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "user",
                "assistant",
                name="assessment_message_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assessment_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_messages_conversation_id",
        "assessment_messages",
        ["conversation_id"],
    )

    op.create_table(
        "assessment_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_prospect_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "structured_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "qualification",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.ForeignKeyConstraint(
            ["campaign_prospect_id"],
            ["campaign_prospects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assessment_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_prospect_id", name="uq_assessment_profiles_link"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index(
        "ix_assessment_profiles_conversation_id",
        "assessment_profiles",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assessment_profiles_conversation_id", table_name="assessment_profiles")
    op.drop_table("assessment_profiles")
    op.drop_index("ix_assessment_messages_conversation_id", table_name="assessment_messages")
    op.drop_table("assessment_messages")
    op.drop_index(
        "ix_assessment_conversations_campaign_prospect_id",
        table_name="assessment_conversations",
    )
    op.drop_table("assessment_conversations")
    op.execute("DROP TYPE IF EXISTS assessment_message_role")
    op.drop_column("campaign_prospects", "assessment_completed_at")
    op.drop_column("ai_reply_settings", "assessment_system_prompt")
