"""Automatic follow-up per stage: one rule for every stage, plus a total limit and a minimum gap.

* outreach_settings.followup_stages  JSON: stage -> {enabled, days, hour, max}. Filled from the rule that is saved today.
* outreach_settings.followup_total_max / followup_gap_days  limits that apply to every person.
* sent_emails.automatic / stage  mark an email queued by the automatic job and the stage it was for, so the limit is
  counted per stage and switching the rule off can cancel what is still waiting.

Revision ID: 053_followup_stages
Revises: 052_followup_schedule
Create Date: 2026-11-18
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053_followup_stages"
down_revision: Union[str, Sequence[str], None] = "052_followup_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAGES = ("not_started", "in_progress", "qualified", "proof", "demo", "value", "calendar", "payment_pending")


def upgrade() -> None:
    op.add_column("outreach_settings", sa.Column("followup_stages", sa.Text(), nullable=True))
    op.add_column("outreach_settings", sa.Column("followup_total_max", sa.Integer(), nullable=False, server_default="6"))
    op.add_column("outreach_settings", sa.Column("followup_gap_days", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("sent_emails", sa.Column("automatic", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("sent_emails", sa.Column("stage", sa.String(length=32), nullable=True))
    conn = op.get_bind()
    row = conn.execute(sa.text(
        "SELECT followup_days, followup_hour, followup_max FROM outreach_settings WHERE id = 1")).first()
    if row is not None:
        days, hour, most = int(row[0] or 2), int(row[1] if row[1] is not None else 18), int(row[2] or 2)
        rules = {k: {"enabled": True, "days": days, "hour": hour, "max": most} for k in _STAGES}
        rules["payment_pending"]["days"] = max(days, 3)  # a payment reminder is never sooner than 3 days anyway
        rules["payment_pending"]["max"] = 3
        conn.execute(sa.text("UPDATE outreach_settings SET followup_stages = :v WHERE id = 1"), {"v": json.dumps(rules)})


def downgrade() -> None:
    op.drop_column("sent_emails", "stage")
    op.drop_column("sent_emails", "automatic")
    for name in ("followup_gap_days", "followup_total_max", "followup_stages"):
        op.drop_column("outreach_settings", name)
