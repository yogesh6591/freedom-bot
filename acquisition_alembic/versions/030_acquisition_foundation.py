"""Autonomous acquisition foundation: Prospect enrichment, ICP, research evidence.

Revision ID: 030_acquisition_foundation
Revises: 029_stripe_checkout_session_id
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "030_acquisition_foundation"
down_revision: Union[str, Sequence[str], None] = "029_stripe_checkout_session_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

acquisition_status = postgresql.ENUM(
    "discovered",
    "researched",
    "ready_for_scoring",
    "scored",
    "ready_for_outreach",
    "needs_more_research",
    "unreachable",
    "excluded",
    name="acquisition_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    acquisition_status.create(bind, checkfirst=True)

    op.add_column("prospects", sa.Column("domain", sa.String(length=255), nullable=True))
    op.add_column("prospects", sa.Column("city", sa.String(length=128), nullable=True))
    op.add_column("prospects", sa.Column("state", sa.String(length=64), nullable=True))
    op.add_column("prospects", sa.Column("region", sa.String(length=128), nullable=True))
    op.add_column("prospects", sa.Column("industry", sa.String(length=255), nullable=True))
    op.add_column("prospects", sa.Column("company_description", sa.Text(), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("locations_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("prospects", sa.Column("authority_type", sa.String(length=64), nullable=True))
    op.add_column("prospects", sa.Column("is_founder_or_equivalent", sa.Boolean(), nullable=True))
    op.add_column("prospects", sa.Column("email_confidence", sa.String(length=32), nullable=True))
    op.add_column("prospects", sa.Column("company_phone", sa.String(length=64), nullable=True))
    op.add_column("prospects", sa.Column("other_contact_path", sa.String(length=512), nullable=True))
    op.add_column("prospects", sa.Column("research_notes", sa.Text(), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("public_signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("prospects", sa.Column("fit_score", sa.Float(), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("fit_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("prospects", sa.Column("timing_score", sa.Float(), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("timing_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("prospects", sa.Column("reachability_score", sa.Float(), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("reachability_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("prospects", sa.Column("priority_score", sa.Float(), nullable=True))
    op.add_column(
        "prospects",
        sa.Column("priority_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("prospects", sa.Column("score_confidence", sa.Float(), nullable=True))
    op.add_column(
        "prospects", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "prospects",
        sa.Column(
            "acquisition_status",
            acquisition_status,
            nullable=False,
            server_default="discovered",
        ),
    )
    op.add_column(
        "prospects", sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "prospects", sa.Column("last_researched_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_index("ix_prospects_domain", "prospects", ["domain"])
    op.create_index("ix_prospects_acquisition_status", "prospects", ["acquisition_status"])
    op.create_index("ix_prospects_priority_score", "prospects", ["priority_score"])

    # Backfill discovered_at from created_at for existing rows.
    op.execute(
        sa.text(
            "UPDATE prospects SET discovered_at = created_at WHERE discovered_at IS NULL"
        )
    )

    op.create_table(
        "prospect_research_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("prospect_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default="other"),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["prospect_id"], ["prospects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_prospect_research_sources_prospect_id",
        "prospect_research_sources",
        ["prospect_id"],
    )
    op.create_index(
        "ix_prospect_research_sources_checked_at",
        "prospect_research_sources",
        ["checked_at"],
    )

    op.create_table(
        "icp_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("geography", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revenue", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("buyer_titles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fit_themes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("priority_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excluded_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score_weights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Seed configurable V1 ICP defaults (US nationwide — not Chicago-only).
    op.execute(
        sa.text(
            """
            INSERT INTO icp_settings (
              id, geography, revenue, buyer_titles, fit_themes,
              priority_categories, excluded_categories, score_weights, notes
            ) VALUES (
              1,
              '{"countries": ["United States"], "nationwide": true, "states": [], "cities": []}'::jsonb,
              '{"primary_min_millions": 5, "primary_max_millions": 50, "sweet_spot_min_millions": 7, "sweet_spot_max_millions": 30, "allow_strong_fit_outside_band": true}'::jsonb,
              '["founder", "co-founder", "owner", "ceo", "chief executive", "managing partner", "principal"]'::jsonb,
              '["expertise-driven white-collar company", "meaningful knowledge-worker density", "organizational complexity", "expensive expertise", "repeatable processes", "reduce human dependency / overhead / fragmented intelligence"]'::jsonb,
              '["fractional CXO", "consultants", "advisory", "agencies", "psychology", "behavioral health", "group practice"]'::jsonb,
              '["generic SMB", "trades", "home services", "contractors", "construction", "restaurants", "retail", "law firm", "accounting firm"]'::jsonb,
              '{"fit": 0.45, "timing": 0.25, "reachability": 0.30, "ready_for_outreach_min_priority": 55, "ready_for_outreach_min_reachability": 40}'::jsonb,
              'V1 ICP for JeanneCAIO / FreedomBot autonomous acquisition. US nationwide; not restricted to Chicago. Editable without code changes.'
            )
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("icp_settings")
    op.drop_index("ix_prospect_research_sources_checked_at", table_name="prospect_research_sources")
    op.drop_index(
        "ix_prospect_research_sources_prospect_id", table_name="prospect_research_sources"
    )
    op.drop_table("prospect_research_sources")

    op.drop_index("ix_prospects_priority_score", table_name="prospects")
    op.drop_index("ix_prospects_acquisition_status", table_name="prospects")
    op.drop_index("ix_prospects_domain", table_name="prospects")

    for col in (
        "last_researched_at",
        "discovered_at",
        "acquisition_status",
        "scored_at",
        "score_confidence",
        "priority_reasons",
        "priority_score",
        "reachability_reasons",
        "reachability_score",
        "timing_reasons",
        "timing_score",
        "fit_reasons",
        "fit_score",
        "public_signals",
        "research_notes",
        "other_contact_path",
        "company_phone",
        "email_confidence",
        "is_founder_or_equivalent",
        "authority_type",
        "locations_json",
        "company_description",
        "industry",
        "region",
        "state",
        "city",
        "domain",
    ):
        op.drop_column("prospects", col)

    acquisition_status.drop(op.get_bind(), checkfirst=True)
