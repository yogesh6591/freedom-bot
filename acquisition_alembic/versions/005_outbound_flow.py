"""Outbound flow: lead sync, settings, events, replies, campaign runtime.

Revision ID: 005_outbound_flow
Revises: 004_campaign_sync
Create Date: 2026-09-22 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_outbound_flow"
down_revision: Union[str, Sequence[str], None] = "004_campaign_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_enum(bind, name: str, *values: str) -> postgresql.ENUM:
    enum = postgresql.ENUM(*values, name=name, create_type=False)
    postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)
    return enum


def upgrade() -> None:
    bind = op.get_bind()

    lead_sync = _create_enum(bind, "lead_sync_status", "not_synced", "synced", "sync_error")
    outreach = _create_enum(
        bind,
        "outreach_status",
        "pending",
        "active",
        "replied",
        "assessment_started",
        "completed",
        "bounced",
        "unsubscribed",
        "cold",
        "stopped",
    )
    assessment = _create_enum(
        bind, "assessment_status", "not_started", "started", "completed"
    )
    cold = _create_enum(bind, "cold_status", "not_cold", "cold")
    runtime = _create_enum(
        bind, "instantly_runtime_status", "not_started", "active", "paused"
    )
    reply_ai = _create_enum(
        bind,
        "reply_ai_status",
        "pending",
        "auto_replied",
        "needs_review",
        "stopped",
        "assessment_started",
        "skipped_auto_reply",
    )
    msg_dir = _create_enum(bind, "reply_message_direction", "inbound", "outbound")

    op.add_column(
        "campaigns",
        sa.Column("instantly_email_list", sa.Text(), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "instantly_runtime_status",
            runtime,
            server_default="not_started",
            nullable=False,
        ),
    )

    op.add_column("campaign_prospects", sa.Column("instantly_lead_id", sa.String(64), nullable=True))
    op.add_column(
        "campaign_prospects",
        sa.Column("sync_status", lead_sync, server_default="not_synced", nullable=False),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("outreach_status", outreach, server_default="pending", nullable=False),
    )
    op.add_column("campaign_prospects", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaign_prospects", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaign_prospects", sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "campaign_prospects",
        sa.Column("assessment_status", assessment, server_default="not_started", nullable=False),
    )
    op.add_column(
        "campaign_prospects",
        sa.Column("cold_status", cold, server_default="not_cold", nullable=False),
    )
    op.add_column("campaign_prospects", sa.Column("assessment_token", sa.String(64), nullable=True))
    op.add_column("campaign_prospects", sa.Column("sync_error", sa.Text(), nullable=True))
    op.add_column(
        "campaign_prospects",
        sa.Column("assessment_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_campaign_prospects_instantly_lead_id", "campaign_prospects", ["instantly_lead_id"])
    op.create_index("ix_campaign_prospects_sync_status", "campaign_prospects", ["sync_status"])
    op.create_index("ix_campaign_prospects_outreach_status", "campaign_prospects", ["outreach_status"])
    op.create_index(
        "ix_campaign_prospects_assessment_token",
        "campaign_prospects",
        ["assessment_token"],
        unique=True,
    )

    op.create_table(
        "outreach_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cold_logic_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("cold_after_days_without_activity", sa.Integer(), server_default="14", nullable=False),
        sa.Column("cold_after_completed_sequence", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO outreach_settings (id, cold_logic_enabled, cold_after_days_without_activity, cold_after_completed_sequence) "
        "VALUES (1, true, 14, true)"
    )

    op.create_table(
        "ai_reply_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("auto_reply_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reply_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("tone_guidance", sa.Text(), server_default="", nullable=False),
        sa.Column("assessment_cta_text", sa.Text(), server_default="You're welcome to take our free assessment when you're ready.", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO ai_reply_settings (id, auto_reply_enabled, reply_prompt, tone_guidance, assessment_cta_text) "
        "VALUES (1, false, 'Respond helpfully and briefly. Always guide the prospect toward the free assessment link.', "
        "'Professional, concise, respectful.', "
        "'You''re welcome to take our free assessment when you''re ready.')"
    )

    op.create_table(
        "provider_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_prospect_id", sa.Uuid(), nullable=True),
        sa.Column("payload_summary", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["campaign_prospect_id"], ["campaign_prospects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_id", name="uq_provider_events_provider_event"),
    )
    op.create_index("ix_provider_events_event_type", "provider_events", ["event_type"])

    op.create_table(
        "reply_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_prospect_id", sa.Uuid(), nullable=False),
        sa.Column("status", reply_ai, server_default="pending", nullable=False),
        sa.Column("latest_incoming_snippet", sa.Text(), nullable=True),
        sa.Column("provider_thread_hint", sa.String(length=255), nullable=True),
        sa.Column("last_provider_email_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["campaign_prospect_id"], ["campaign_prospects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_prospect_id", name="uq_reply_conversations_campaign_prospect"),
    )
    op.create_index("ix_reply_conversations_status", "reply_conversations", ["status"])

    op.create_table(
        "reply_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("direction", msg_dir, nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("provider_email_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["reply_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reply_messages_conversation_id", "reply_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_reply_messages_conversation_id", table_name="reply_messages")
    op.drop_table("reply_messages")
    op.drop_index("ix_reply_conversations_status", table_name="reply_conversations")
    op.drop_table("reply_conversations")
    op.drop_index("ix_provider_events_event_type", table_name="provider_events")
    op.drop_table("provider_events")
    op.drop_table("ai_reply_settings")
    op.drop_table("outreach_settings")
    op.drop_index("ix_campaign_prospects_assessment_token", table_name="campaign_prospects")
    op.drop_index("ix_campaign_prospects_outreach_status", table_name="campaign_prospects")
    op.drop_index("ix_campaign_prospects_sync_status", table_name="campaign_prospects")
    op.drop_index("ix_campaign_prospects_instantly_lead_id", table_name="campaign_prospects")
    op.drop_column("campaign_prospects", "assessment_started_at")
    op.drop_column("campaign_prospects", "sync_error")
    op.drop_column("campaign_prospects", "assessment_token")
    op.drop_column("campaign_prospects", "cold_status")
    op.drop_column("campaign_prospects", "assessment_status")
    op.drop_column("campaign_prospects", "last_reply_at")
    op.drop_column("campaign_prospects", "last_activity_at")
    op.drop_column("campaign_prospects", "last_synced_at")
    op.drop_column("campaign_prospects", "outreach_status")
    op.drop_column("campaign_prospects", "sync_status")
    op.drop_column("campaign_prospects", "instantly_lead_id")
    op.drop_column("campaigns", "instantly_runtime_status")
    op.drop_column("campaigns", "instantly_email_list")
    for name in (
        "reply_message_direction",
        "reply_ai_status",
        "instantly_runtime_status",
        "cold_status",
        "assessment_status",
        "outreach_status",
        "lead_sync_status",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
