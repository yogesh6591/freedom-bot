"""Read-only access to the retired icp_settings row. The table is kept; nothing writes it."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from acquisition.models.prospect import IcpSettings

DEFAULT_GEOGRAPHY: dict[str, Any] = {
    "countries": ["United States"],
    "nationwide": True,
    "states": [],
    "cities": [],
}

DEFAULT_REVENUE: dict[str, Any] = {
    "primary_min_millions": 5,
    "primary_max_millions": 50,
    "sweet_spot_min_millions": 7,
    "sweet_spot_max_millions": 30,
    "allow_strong_fit_outside_band": True,
}

DEFAULT_BUYER_TITLES: list[str] = [
    "founder",
    "co-founder",
    "owner",
    "ceo",
    "chief executive",
    "managing partner",
    "principal",
]

DEFAULT_FIT_THEMES: list[str] = [
    "expertise-driven white-collar company",
    "meaningful knowledge-worker density",
    "organizational complexity",
    "expensive expertise",
    "repeatable processes",
    "reduce human dependency / overhead / fragmented intelligence",
]

DEFAULT_PRIORITY_CATEGORIES: list[str] = [
    "fractional CXO",
    "consultants",
    "advisory",
    "agencies",
    "psychology",
    "behavioral health",
    "group practice",
]

DEFAULT_EXCLUDED_CATEGORIES: list[str] = [
    "generic SMB",
    "trades",
    "home services",
    "contractors",
    "construction",
    "restaurants",
    "retail",
    "law firm",
    "accounting firm",
]

DEFAULT_SCORE_WEIGHTS: dict[str, Any] = {
    "fit": 0.45,
    "timing": 0.25,
    "reachability": 0.30,
    "ready_for_outreach_min_priority": 55,
    "ready_for_outreach_min_reachability": 40,
    "allow_generic_inbox": False,
    "outreach_channel": "email",
}


def default_icp_payload() -> dict[str, Any]:
    return {
        "geography": deepcopy(DEFAULT_GEOGRAPHY),
        "revenue": deepcopy(DEFAULT_REVENUE),
        "buyer_titles": list(DEFAULT_BUYER_TITLES),
        "fit_themes": list(DEFAULT_FIT_THEMES),
        "priority_categories": list(DEFAULT_PRIORITY_CATEGORIES),
        "excluded_categories": list(DEFAULT_EXCLUDED_CATEGORIES),
        "score_weights": deepcopy(DEFAULT_SCORE_WEIGHTS),
        "notes": (
            "Retired ICP row. Targeting now lives in Prospecting rules. "
            "This table is not written."
        ),
    }


class IcpSettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self) -> IcpSettings | None:
        return self.db.get(IcpSettings, 1)

    def as_dict(self) -> dict[str, Any]:
        row = self.get()
        if row is None:
            defaults = default_icp_payload()
            return {
                **defaults,
                "autonomous_campaign_id": None,
                "activation_batch_limit": 5,
                "engine_enabled": False,
                "discovery_enabled": False,
                "discovery_cycle_limit": 5,
                "discovery_interval_minutes": 1440,
                "autonomous_outreach_enabled": False,
                "activation_interval_minutes": 1440,
                "capacity_mode": "auto",
                "capacity_upcoming_meetings_reduced_at": 5,
                "capacity_upcoming_meetings_paused_at": 10,
                "capacity_reduced_activation_limit": 2,
                "allow_mock_discovery_in_production": False,
                "learning_enabled": False,
                "learning_min_sample_size": 25,
                "learning_max_adjustment": 8.0,
                "learning_metrics_json": None,
                "learning_recomputed_at": None,
                "learning_profile_version": 0,
                "learning_model": "gpt-4o-mini",
                "updated_at": None,
            }
        return {
            "geography": row.geography or deepcopy(DEFAULT_GEOGRAPHY),
            "revenue": row.revenue or deepcopy(DEFAULT_REVENUE),
            "buyer_titles": list(row.buyer_titles or []),
            "fit_themes": list(row.fit_themes or []),
            "priority_categories": list(row.priority_categories or []),
            "excluded_categories": list(row.excluded_categories or []),
            "score_weights": row.score_weights or deepcopy(DEFAULT_SCORE_WEIGHTS),
            "notes": row.notes,
            "autonomous_campaign_id": (
                str(row.autonomous_campaign_id) if row.autonomous_campaign_id else None
            ),
            "activation_batch_limit": int(row.activation_batch_limit or 5),
            "engine_enabled": bool(row.discovery_enabled or row.autonomous_outreach_enabled),
            "discovery_enabled": bool(row.discovery_enabled),
            "discovery_cycle_limit": int(row.discovery_cycle_limit or 5),
            "discovery_interval_minutes": int(row.discovery_interval_minutes or 1440),
            "autonomous_outreach_enabled": bool(row.autonomous_outreach_enabled),
            "activation_interval_minutes": int(row.activation_interval_minutes or 1440),
            "capacity_mode": (row.capacity_mode or "auto"),
            "capacity_upcoming_meetings_reduced_at": int(
                row.capacity_upcoming_meetings_reduced_at or 5
            ),
            "capacity_upcoming_meetings_paused_at": int(
                row.capacity_upcoming_meetings_paused_at or 10
            ),
            "capacity_reduced_activation_limit": int(
                row.capacity_reduced_activation_limit or 2
            ),
            "allow_mock_discovery_in_production": bool(
                row.allow_mock_discovery_in_production
            ),
            "learning_enabled": bool(row.learning_enabled),
            "learning_min_sample_size": int(row.learning_min_sample_size or 25),
            "learning_max_adjustment": float(row.learning_max_adjustment or 8),
            "learning_metrics_json": row.learning_metrics_json,
            "learning_recomputed_at": (
                row.learning_recomputed_at.isoformat() if row.learning_recomputed_at else None
            ),
            "learning_profile_version": int(getattr(row, "learning_profile_version", 0) or 0),
            "learning_model": (
                str(getattr(row, "learning_model", None) or "gpt-4o-mini").strip()
                or "gpt-4o-mini"
            ),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
