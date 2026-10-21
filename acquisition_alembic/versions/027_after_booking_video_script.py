"""After-booking recommended welcome/prep video script (Admin).

Revision ID: 027_after_booking_video_script
Revises: 026_qualification_system_prompt
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027_after_booking_video_script"
down_revision: Union[str, Sequence[str], None] = "026_qualification_system_prompt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_SCRIPT = """Hi, I’m Jeanne.

Thank you for completing the FreedomBot assessment and booking time with me.

By the time we meet, I’ll already have the business context you shared through the assessment, including the areas where there may be inefficiencies, delays, unnecessary dependency on key people, or opportunities to create more leverage.

That means we won’t need to start the conversation from zero or repeat all of the basic discovery.

Our meeting is really about your specific business — what is happening today, what may be worth changing, where FreedomBot may or may not fit, and what the right next step could look like.

Before we meet, think about the areas in your company where you find yourself asking:

“Can FreedomBot do this?”

That might be a workflow, a handoff, an approval process, internal knowledge, repetitive work, reporting, follow-up, or another area where the business depends too heavily on people or fragmented systems.

Bring those questions with you.

We’ll use your assessment as the starting point and focus the conversation on what makes sense for your company.

I’m looking forward to speaking with you."""


def upgrade() -> None:
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_video_script", sa.Text(), nullable=True),
    )
    op.get_bind().execute(
        sa.text(
            """
            UPDATE journey_settings
            SET after_booking_video_script = :script
            WHERE id = 1
              AND (
                after_booking_video_script IS NULL
                OR BTRIM(after_booking_video_script) = ''
              )
            """
        ),
        {"script": _DEFAULT_SCRIPT},
    )


def downgrade() -> None:
    op.drop_column("journey_settings", "after_booking_video_script")
