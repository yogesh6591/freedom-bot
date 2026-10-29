"""Outreach log: every email we send (or queue) to a prospect.

Revision ID: 044_outreach_log
Revises: 043_prospecting_rules
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "044_outreach_log"
down_revision: Union[str, Sequence[str], None] = "043_prospecting_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sent_emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "prospect_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prospects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "campaign_prospect_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_prospects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="first"),
        sa.Column(
            "follow_up_of_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sent_emails.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column("gmail_message_id", sa.String(length=128), nullable=True),
        sa.Column("gmail_thread_id", sa.String(length=128), nullable=True),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sent_emails_prospect_id", "sent_emails", ["prospect_id"])
    op.create_index("ix_sent_emails_status_queued", "sent_emails", ["status", "queued_at"])
    op.create_index("ix_sent_emails_batch_id", "sent_emails", ["batch_id"])
    op.create_index("ix_sent_emails_sent_at", "sent_emails", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_sent_emails_sent_at", table_name="sent_emails")
    op.drop_index("ix_sent_emails_batch_id", table_name="sent_emails")
    op.drop_index("ix_sent_emails_status_queued", table_name="sent_emails")
    op.drop_index("ix_sent_emails_prospect_id", table_name="sent_emails")
    op.drop_table("sent_emails")
