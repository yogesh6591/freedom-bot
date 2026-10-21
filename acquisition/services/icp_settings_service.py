"""Configurable ICP + autonomous engine settings singleton."""

from __future__ import annotations

import json
import uuid
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
    # Email outreach: whether info@/hello@/etc. counts as a ready contact path.
    "allow_generic_inbox": False,
    "outreach_channel": "email",
}

_BOOL_TRUE = {"1", "true", "yes", "on"}


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
            "V1 ICP for JeanneCAIO / FreedomBot autonomous acquisition. "
            "US nationwide; not restricted to Chicago. Editable without code changes."
        ),
    }


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _BOOL_TRUE


class IcpSettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(self) -> IcpSettings:
        row = self.db.get(IcpSettings, 1)
        if row is not None:
            return row
        defaults = default_icp_payload()
        row = IcpSettings(
            id=1,
            **{k: defaults[k] for k in defaults if k != "notes"},
            notes=defaults["notes"],
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, **fields: Any) -> IcpSettings:
        row = self.get_or_create()
        allowed = {
            "geography",
            "revenue",
            "buyer_titles",
            "fit_themes",
            "priority_categories",
            "excluded_categories",
            "score_weights",
            "notes",
            "autonomous_campaign_id",
            "activation_batch_limit",
            "engine_enabled",
            "discovery_enabled",
            "discovery_cycle_limit",
            "discovery_interval_minutes",
            "discovery_sources",
            "autonomous_outreach_enabled",
            "activation_interval_minutes",
            "capacity_mode",
            "capacity_upcoming_meetings_reduced_at",
            "capacity_upcoming_meetings_paused_at",
            "capacity_reduced_activation_limit",
            "allow_mock_discovery_in_production",
            "learning_enabled",
            "learning_min_sample_size",
            "learning_max_adjustment",
            "learning_metrics_json",
            "learning_recomputed_at",
            "learning_profile_version",
            "learning_model",
        }
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"buyer_titles", "fit_themes", "priority_categories", "excluded_categories"}:
                if isinstance(value, str):
                    value = [line.strip() for line in value.splitlines() if line.strip()]
            if key in {"geography", "revenue", "score_weights"} and isinstance(value, str):
                value = json.loads(value)
            if key == "autonomous_campaign_id":
                if value in (None, "", "none"):
                    value = None
                elif isinstance(value, str):
                    value = uuid.UUID(value)
            if key in {
                "activation_batch_limit",
                "discovery_cycle_limit",
                "discovery_interval_minutes",
                "activation_interval_minutes",
                "capacity_upcoming_meetings_reduced_at",
                "capacity_upcoming_meetings_paused_at",
                "capacity_reduced_activation_limit",
                "learning_min_sample_size",
            }:
                value = max(0, int(value or 0))
                if key.endswith("_limit") or key.endswith("_minutes"):
                    value = max(1, value) if "interval" in key or "batch" in key or "cycle" in key or "reduced_activation" in key else value
                if key == "capacity_reduced_activation_limit":
                    value = max(0, int(value))
                if key in {"discovery_cycle_limit", "activation_batch_limit"}:
                    value = max(1, int(value or 5))
                if "interval_minutes" in key:
                    value = max(1, int(value or 1440))
                if key == "learning_min_sample_size":
                    value = max(1, min(500, int(value or 25)))
            if key == "learning_max_adjustment":
                value = max(0.0, min(25.0, float(value or 8)))
            if key == "learning_profile_version":
                value = max(0, int(value or 0))
            if key == "learning_model":
                from acquisition.services.ai_outcome_learning_service import ALLOWED_MODELS

                text = str(value or "gpt-4o-mini").strip() or "gpt-4o-mini"
                value = text if text in ALLOWED_MODELS else "gpt-4o-mini"
            if key in {
                "engine_enabled",
                "discovery_enabled",
                "autonomous_outreach_enabled",
                "allow_mock_discovery_in_production",
                "learning_enabled",
            }:
                value = _as_bool(value, default=False)
            if key == "capacity_mode":
                mode = str(value or "auto").strip().lower()
                value = mode if mode in {"auto", "normal", "reduced", "paused"} else "auto"
            setattr(row, key, value)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def as_dict(self) -> dict[str, Any]:
        row = self.get_or_create()
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
            # Legacy master flag is derived from the two production controls.
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
