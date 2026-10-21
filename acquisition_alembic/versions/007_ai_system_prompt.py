"""Merge AI reply prompt fields into system_prompt.

Revision ID: 007_ai_system_prompt
Revises: 006_openai_provider
Create Date: 2026-09-22 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007_ai_system_prompt"
down_revision: Union[str, Sequence[str], None] = "006_openai_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_SYSTEM_PROMPT = (
    "Respond helpfully and briefly. Always guide the prospect toward the free assessment link.\n\n"
    "Tone: Professional, concise, respectful.\n\n"
    "When sharing the assessment, use this CTA: "
    "You're welcome to take our free assessment when you're ready."
)


def upgrade() -> None:
    op.add_column(
        "ai_reply_settings",
        sa.Column("system_prompt", sa.Text(), server_default="", nullable=False),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, reply_prompt, tone_guidance, assessment_cta_text "
            "FROM ai_reply_settings"
        )
    ).mappings().all()

    for row in rows:
        parts: list[str] = []
        reply = (row["reply_prompt"] or "").strip()
        tone = (row["tone_guidance"] or "").strip()
        cta = (row["assessment_cta_text"] or "").strip()
        if reply:
            parts.append(reply)
        if tone:
            parts.append(f"Tone: {tone}")
        if cta:
            parts.append(f"When sharing the assessment, use this CTA: {cta}")
        merged = "\n\n".join(parts).strip() or DEFAULT_SYSTEM_PROMPT
        conn.execute(
            sa.text("UPDATE ai_reply_settings SET system_prompt = :prompt WHERE id = :id"),
            {"prompt": merged, "id": row["id"]},
        )

    op.alter_column("ai_reply_settings", "system_prompt", server_default=None)
    op.drop_column("ai_reply_settings", "reply_prompt")
    op.drop_column("ai_reply_settings", "tone_guidance")
    op.drop_column("ai_reply_settings", "assessment_cta_text")


def downgrade() -> None:
    op.add_column(
        "ai_reply_settings",
        sa.Column("reply_prompt", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "ai_reply_settings",
        sa.Column("tone_guidance", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "ai_reply_settings",
        sa.Column(
            "assessment_cta_text",
            sa.Text(),
            server_default="You're welcome to take our free assessment when you're ready.",
            nullable=False,
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, system_prompt FROM ai_reply_settings")
    ).mappings().all()
    for row in rows:
        prompt = (row["system_prompt"] or "").strip()
        conn.execute(
            sa.text(
                "UPDATE ai_reply_settings SET "
                "reply_prompt = :reply, tone_guidance = :tone, assessment_cta_text = :cta "
                "WHERE id = :id"
            ),
            {
                "reply": prompt or "Respond helpfully and briefly.",
                "tone": "Professional, concise, respectful.",
                "cta": "You're welcome to take our free assessment when you're ready.",
                "id": row["id"],
            },
        )

    op.drop_column("ai_reply_settings", "system_prompt")
