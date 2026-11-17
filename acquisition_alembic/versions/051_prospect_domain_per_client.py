"""One found website per organisation, not one across all organisations.

The old rule (uq_prospects_discovered_domain, added in 036) allowed a found website to be saved once in the whole
system. Now that prospects belong to an organisation (client_id, 050), two organisations may each hold the same
company; inside one organisation it still cannot be saved twice.

Revision ID: 051_prospect_domain_per_client
Revises: 050_prospect_client_id
Create Date: 2026-11-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "051_prospect_domain_per_client"
down_revision: Union[str, Sequence[str], None] = "050_prospect_client_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = "discovery_source IS NOT NULL AND domain IS NOT NULL"


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_prospects_discovered_domain")
    op.create_index(
        "uq_prospects_discovered_domain_client",
        "prospects",
        ["client_id", "domain"],
        unique=True,
        postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_prospects_discovered_domain_client")
    dup = op.get_bind().execute(sa.text(
        f"SELECT 1 FROM prospects WHERE {_WHERE} GROUP BY domain HAVING COUNT(*) > 1 LIMIT 1")).fetchone()
    if dup is None:
        op.create_index(
            "uq_prospects_discovered_domain", "prospects", ["domain"], unique=True,
            postgresql_where=sa.text(_WHERE))
    else:
        print("[051] WARNING: the same website is saved in more than one organisation; the one-per-system index "
              "was not recreated.")
