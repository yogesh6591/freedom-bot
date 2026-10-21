"""Optional welcome-video poster for After Booking.

Revision ID: 019_after_booking_poster
Revises: 018_after_booking
Create Date: 2026-09-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019_after_booking_poster"
down_revision: Union[str, Sequence[str], None] = "018_after_booking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "journey_settings",
        sa.Column("after_booking_video_poster_url", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("journey_settings", "after_booking_video_poster_url")
