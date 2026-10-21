"""Assessment field-state helpers and qualification enums (not sales-journey content)."""

from __future__ import annotations

import enum
from typing import Any


class FieldStatus(str, enum.Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    DECLINED = "declined"
    NOT_APPLICABLE = "not_applicable"
    MISSING = "missing"


class QualificationStatus(str, enum.Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"


STRUCTURED_FIELD_KEYS = (
    "company",
    "goals",
    "revenue_range",
    "headcount",
    "functions",
    "tools",
    "workflows",
    "knowledge_locations",
    "pain_points",
    "time_cost_impact",
    "bottlenecks",
    "change_readiness",
    "ai_readiness",
    "authority",
)


def empty_field() -> dict[str, Any]:
    return {"value": None, "status": FieldStatus.MISSING.value}


def empty_structured_state() -> dict[str, Any]:
    return {key: empty_field() for key in STRUCTURED_FIELD_KEYS}


def empty_qualification() -> dict[str, Any]:
    """Internal qualification payload at assessment completion.

    ``status`` / ``assessment_status`` = qualified means eligible for the
    Result → Proof → Demo → Value branch — not calendar-ready and not dependent
    on verbal "continue to proof/demo" assent. ``budget_readiness`` stays
    ``not_assessed`` until after value/price conditioning.
    """
    pending = QualificationStatus.PENDING.value
    return {
        "icp_fit": None,
        "complexity": None,
        "real_money_problem": None,
        "authority": None,
        "authority_role": None,
        "growth_change_readiness": None,
        "openness_to_ai": None,
        # Revenue may support economic_capacity; never confirm budget here.
        "economic_capacity": None,
        "budget_readiness": "not_assessed",
        # Legacy alias — prefer economic_capacity. Not confirmed ability-to-pay.
        "ability_to_pay": None,
        "disqualifiers": [],
        "signal_reasons": {},
        "notes": None,
        "explanation": None,
        "status": pending,
        "assessment_status": pending,
    }


__all__ = [
    "FieldStatus",
    "QualificationStatus",
    "STRUCTURED_FIELD_KEYS",
    "empty_field",
    "empty_qualification",
    "empty_structured_state",
]
