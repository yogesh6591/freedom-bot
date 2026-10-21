"""036 — Discovery engine v2: slice ledger, live trace events, company identity.

Revision ID: 036_discovery_engine_v2
Revises: 035_ai_outcome_learning_audit
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "036_discovery_engine_v2"
down_revision: Union[str, Sequence[str], None] = "035_ai_outcome_learning_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("npi", sa.String(length=20), nullable=True))
    op.add_column("prospects", sa.Column("discovery_source", sa.String(length=64), nullable=True))
    op.add_column("prospects", sa.Column("discovery_slice_key", sa.String(length=255), nullable=True))
    op.add_column("prospects", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prospects", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("prospects", sa.Column("website_verified", sa.Boolean(), nullable=True))
    op.add_column("prospects", sa.Column("business_type", sa.String(length=128), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("classification_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_prospects_npi", "prospects", ["npi"])
    op.create_index("ix_prospects_discovery_source", "prospects", ["discovery_source"])

    op.add_column(
        "icp_settings",
        sa.Column("discovery_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # Backfill discovery provenance for rows the old engine created.
    op.execute(
        "UPDATE prospects SET discovery_source = 'legacy_search', "
        "first_seen_at = COALESCE(discovered_at, created_at), "
        "last_seen_at = COALESCE(discovered_at, created_at), times_seen = 1 "
        "WHERE source = 'autonomous_discovery' AND discovery_source IS NULL"
    )

    # Collapse subdomains to the registrable root so dedupe uses one key.
    bind = op.get_bind()
    try:
        from acquisition.services.domain_tools import root_domain

        rows = bind.execute(sa.text("SELECT id, domain FROM prospects WHERE domain IS NOT NULL")).fetchall()
        for row in rows:
            root = root_domain(row.domain)
            if root and root != row.domain:
                bind.execute(
                    sa.text("UPDATE prospects SET domain = :d WHERE id = :i"),
                    {"d": root, "i": row.id},
                )
    except Exception as exc:  # noqa: BLE001
        print(f"[036] domain backfill skipped: {exc}")

    # One discovered company per domain / NPI. Partial so manual multi-contact
    # imports (several people at one company) keep working.
    dup = bind.execute(
        sa.text(
            "SELECT domain FROM prospects WHERE discovery_source IS NOT NULL AND domain IS NOT NULL "
            "GROUP BY domain HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).fetchone()
    if dup is None:
        op.create_index(
            "uq_prospects_discovered_domain",
            "prospects",
            ["domain"],
            unique=True,
            postgresql_where=sa.text("discovery_source IS NOT NULL AND domain IS NOT NULL"),
        )
    else:
        print(
            "[036] WARNING: duplicate discovered domains exist; unique index not created. "
            "Merge duplicates, then re-run: CREATE UNIQUE INDEX uq_prospects_discovered_domain "
            "ON prospects(domain) WHERE discovery_source IS NOT NULL AND domain IS NOT NULL"
        )
    op.create_index(
        "uq_prospects_npi",
        "prospects",
        ["npi"],
        unique=True,
        postgresql_where=sa.text("npi IS NOT NULL"),
    )

    op.create_table(
        "discovery_slices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("angle", sa.String(length=128), nullable=True),
        sa.Column("cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exhausted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rest_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_discovery_slices_source_last_run", "discovery_slices", ["source", "last_run_at"])

    op.create_table(
        "acquisition_trace_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("prospect_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_acquisition_trace_events_run_seq", "acquisition_trace_events", ["run_id", "seq"])
    op.create_index("ix_acquisition_trace_events_created", "acquisition_trace_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_acquisition_trace_events_created", table_name="acquisition_trace_events")
    op.drop_index("ix_acquisition_trace_events_run_seq", table_name="acquisition_trace_events")
    op.drop_table("acquisition_trace_events")
    op.drop_index("ix_discovery_slices_source_last_run", table_name="discovery_slices")
    op.drop_table("discovery_slices")
    op.execute("DROP INDEX IF EXISTS uq_prospects_npi")
    op.execute("DROP INDEX IF EXISTS uq_prospects_discovered_domain")
    op.drop_column("icp_settings", "discovery_sources")
    op.drop_index("ix_prospects_discovery_source", table_name="prospects")
    op.drop_index("ix_prospects_npi", table_name="prospects")
    for col in (
        "classification_json",
        "business_type",
        "website_verified",
        "times_seen",
        "last_seen_at",
        "first_seen_at",
        "discovery_slice_key",
        "discovery_source",
        "npi",
    ):
        op.drop_column("prospects", col)
