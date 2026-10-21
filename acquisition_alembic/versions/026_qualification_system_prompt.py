"""Add editable qualification system prompt to AI settings.

Revision ID: 026_qualification_system_prompt
Revises: 025_drop_demo_static_assessment
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026_qualification_system_prompt"
down_revision: Union[str, Sequence[str], None] = "025_drop_demo_static_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from acquisition.models.settings import DEFAULT_QUALIFICATION_SYSTEM_PROMPT

    op.add_column(
        "ai_reply_settings",
        sa.Column(
            "qualification_system_prompt",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE ai_reply_settings SET qualification_system_prompt = :prompt "
            "WHERE qualification_system_prompt = '' OR qualification_system_prompt IS NULL"
        ),
        {"prompt": DEFAULT_QUALIFICATION_SYSTEM_PROMPT},
    )
    op.alter_column("ai_reply_settings", "qualification_system_prompt", server_default=None)


def downgrade() -> None:
    op.drop_column("ai_reply_settings", "qualification_system_prompt")
