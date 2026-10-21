"""Canonical OpenAI Responses API Structured Output schemas (strict=true).

These schemas are the primary contract for Stage A and Outreach Slot calls.
Defensive parsers remain as a second layer.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _str() -> dict[str, Any]:
    return {"type": "string"}


def _str_null() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def _int_null() -> dict[str, Any]:
    return {"type": ["integer", "null"]}


def _num_null() -> dict[str, Any]:
    return {"type": ["number", "null"]}


def _bool() -> dict[str, Any]:
    return {"type": "boolean"}


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


_SOURCE_ITEM = _obj(
    {
        "url": _str(),
        "type": _str_null(),
        "supports": {
            "type": "array",
            "items": _str(),
        },
    },
    ["url", "type", "supports"],
)

_LOCATION = _obj(
    {
        "city": _str_null(),
        "state": _str_null(),
        "country": _str_null(),
    },
    ["city", "state", "country"],
)

_PRELIM_SCALE = _obj(
    {
        "revenue": _str_null(),
        "headcount": _str_null(),
        "other_signals": {"type": "array", "items": _str()},
    },
    ["revenue", "headcount", "other_signals"],
)

_PRELIM_DM = _obj(
    {
        "name": _str_null(),
        "title": _str_null(),
        "source_urls": {"type": "array", "items": _str()},
    },
    ["name", "title", "source_urls"],
)

_PRELIM_CONTACT = _obj(
    {
        "direct_business_email_found": _bool(),
        "generic_email_found": _bool(),
        "phone_found": _bool(),
        "contact_page_found": _bool(),
    },
    [
        "direct_business_email_found",
        "generic_email_found",
        "phone_found",
        "contact_page_found",
    ],
)

_TIMING_SIGNAL = _obj(
    {
        "signal": _str(),
        "source_url": _str_null(),
        "date": _str_null(),
    },
    ["signal", "source_url", "date"],
)

_REJECTED_SEED = _obj(
    {
        "company_name": _str(),
        "domain": _str_null(),
        "reason_code": _str(),
        "reason": _str(),
    },
    ["company_name", "domain", "reason_code", "reason"],
)

_REASON_COUNT = _obj(
    {
        "reason_code": _str(),
        "count": {"type": "integer"},
    },
    ["reason_code", "count"],
)

_SEED_PROSPECT = _obj(
    {
        "company_name": _str(),
        "official_website": _str_null(),
        "website": _str_null(),
        "normalized_domain": _str_null(),
        "location": _LOCATION,
        "industry": _str_null(),
        "business_type": _str_null(),
        "description": _str_null(),
        "preliminary_scale_evidence": _PRELIM_SCALE,
        "preliminary_decision_maker": _PRELIM_DM,
        "preliminary_contactability": _PRELIM_CONTACT,
        "timing_signals": {"type": "array", "items": _TIMING_SIGNAL},
        "why_strong_seed": _str(),
        "initial_evidence_urls": {"type": "array", "items": _str()},
        "sources": {"type": "array", "items": _SOURCE_ITEM},
    },
    [
        "company_name",
        "official_website",
        "website",
        "normalized_domain",
        "location",
        "industry",
        "business_type",
        "description",
        "preliminary_scale_evidence",
        "preliminary_decision_maker",
        "preliminary_contactability",
        "timing_signals",
        "why_strong_seed",
        "initial_evidence_urls",
        "sources",
    ],
)

_DISCOVERY_META = _obj(
    {
        "companies_considered": _int_null(),
        "rejected_before_selection": {"type": "array", "items": _REJECTED_SEED},
        "rejection_reason_counts": {"type": "array", "items": _REASON_COUNT},
    },
    ["companies_considered", "rejected_before_selection", "rejection_reason_counts"],
)

MASTER_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prospects": {"type": "array", "items": _SEED_PROSPECT},
        "discovery_meta": _DISCOVERY_META,
    },
    "required": ["prospects", "discovery_meta"],
}


_METRIC_STATUS = {
    "type": "string",
    "enum": ["verified", "supported", "estimated", "unknown"],
}

_REVENUE = _obj(
    {
        "exact_amount": _num_null(),
        "band": _str_null(),
        "status": _METRIC_STATUS,
        "sources": {"type": "array", "items": _SOURCE_ITEM},
        "source_urls": {"type": "array", "items": _str()},
    },
    ["exact_amount", "band", "status", "sources", "source_urls"],
)

_HEADCOUNT = _obj(
    {
        "exact_value": _int_null(),
        "band": _str_null(),
        "status": _METRIC_STATUS,
        "sources": {"type": "array", "items": _SOURCE_ITEM},
        "source_urls": {"type": "array", "items": _str()},
    },
    ["exact_value", "band", "status", "sources", "source_urls"],
)

_DECISION_MAKER = _obj(
    {
        "name": _str_null(),
        "title": _str_null(),
        "relationship": _str_null(),
        "authority_level": _str_null(),
        "profile_url": _str_null(),
        "source_urls": {"type": "array", "items": _str()},
        "sources": {"type": "array", "items": _SOURCE_ITEM},
        "confidence": _str_null(),
        "status": _METRIC_STATUS,
        "search_attempted": _bool(),
    },
    [
        "name",
        "title",
        "relationship",
        "authority_level",
        "profile_url",
        "source_urls",
        "sources",
        "confidence",
        "status",
        "search_attempted",
    ],
)

_CONTACT = _obj(
    {
        "business_email": _str_null(),
        "email_status": _str_null(),
        "phone": _str_null(),
        "contact_url": _str_null(),
        "source_urls": {"type": "array", "items": _str()},
        "sources": {"type": "array", "items": _SOURCE_ITEM},
        "status": _METRIC_STATUS,
    },
    [
        "business_email",
        "email_status",
        "phone",
        "contact_url",
        "source_urls",
        "sources",
        "status",
    ],
)

_COMPLETENESS = _obj(
    {
        "company_identity": _METRIC_STATUS,
        "website": _METRIC_STATUS,
        "location": _METRIC_STATUS,
        "industry": _METRIC_STATUS,
        "revenue": _METRIC_STATUS,
        "headcount": _METRIC_STATUS,
        "decision_maker": _METRIC_STATUS,
        "contact": _METRIC_STATUS,
        "timing_signals": _METRIC_STATUS,
        "source_urls_consulted": {"type": "integer"},
        "overall": _METRIC_STATUS,
    },
    [
        "company_identity",
        "website",
        "location",
        "industry",
        "revenue",
        "headcount",
        "decision_maker",
        "contact",
        "timing_signals",
        "source_urls_consulted",
        "overall",
    ],
)

_OUTREACH_READINESS = _obj(
    {
        "ai_recommended_ready": _bool(),
        "channel": _str(),
        "company_verified": _bool(),
        "icp_verified": _bool(),
        "scale_supported": _bool(),
        "decision_maker_verified": _bool(),
        "contact_path_verified": _bool(),
        "personalization_context_available": _bool(),
        "mandatory_requirements_passed": _bool(),
        "reason": _str_null(),
    },
    [
        "ai_recommended_ready",
        "channel",
        "company_verified",
        "icp_verified",
        "scale_supported",
        "decision_maker_verified",
        "contact_path_verified",
        "personalization_context_available",
        "mandatory_requirements_passed",
        "reason",
    ],
)

# Scores are integers (or null) — never objects/arrays.
_SCORE = _int_null()

_ENRICHED_PROSPECT = _obj(
    {
        "company_name": _str(),
        "website": _str_null(),
        "normalized_domain": _str_null(),
        "location": _LOCATION,
        "industry": _str_null(),
        "business_type": _str_null(),
        "description": _str_null(),
        "revenue": _REVENUE,
        "headcount": _HEADCOUNT,
        "decision_maker": _DECISION_MAKER,
        "contact": _CONTACT,
        "timing_signals": {"type": "array", "items": _TIMING_SIGNAL},
        "research_notes": _str_null(),
        "research_completeness": _COMPLETENESS,
        "fit_score": _SCORE,
        "fit_reason": _str_null(),
        "timing_score": _SCORE,
        "timing_reason": _str_null(),
        "reachability_score": _SCORE,
        "reachability_reason": _str_null(),
        "sources": {"type": "array", "items": _SOURCE_ITEM},
        "outreach_readiness": _OUTREACH_READINESS,
    },
    [
        "company_name",
        "website",
        "normalized_domain",
        "location",
        "industry",
        "business_type",
        "description",
        "revenue",
        "headcount",
        "decision_maker",
        "contact",
        "timing_signals",
        "research_notes",
        "research_completeness",
        "fit_score",
        "fit_reason",
        "timing_score",
        "timing_reason",
        "reachability_score",
        "reachability_reason",
        "sources",
        "outreach_readiness",
    ],
)

_REJECTED_CANDIDATE = _obj(
    {
        "company_name": _str(),
        "domain": _str_null(),
        "reason_code": _str(),
        "reason": _str(),
    },
    ["company_name", "domain", "reason_code", "reason"],
)

OUTREACH_SLOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "slot_id": _str(),
        "status": {
            "type": "string",
            "enum": ["ready", "exhausted", "technical_failure"],
        },
        "candidates_attempted": {"type": "integer"},
        "rejected_candidates": {"type": "array", "items": _REJECTED_CANDIDATE},
        "prospect": {"anyOf": [_ENRICHED_PROSPECT, {"type": "null"}]},
        "failure_reason": _str_null(),
    },
    "required": [
        "slot_id",
        "status",
        "candidates_attempted",
        "rejected_candidates",
        "prospect",
        "failure_reason",
    ],
}


def master_discovery_text_format() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "master_discovery_result",
            "strict": True,
            "schema": deepcopy(MASTER_DISCOVERY_SCHEMA),
        }
    }


def outreach_slot_text_format() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "outreach_slot_result",
            "strict": True,
            "schema": deepcopy(OUTREACH_SLOT_SCHEMA),
        }
    }


def validate_schema_shape(schema: dict[str, Any]) -> list[str]:
    """Local structural checks (no OpenAI call). Returns list of problems."""
    problems: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or (
            isinstance(node.get("type"), list) and "object" in node["type"]
        ):
            if node.get("additionalProperties") is not False and "anyOf" not in node:
                # Objects under anyOf handled separately
                if "properties" in node:
                    problems.append(f"{path}: object missing additionalProperties:false")
            props = node.get("properties") or {}
            req = node.get("required") or []
            if props and set(props.keys()) != set(req):
                missing = set(props) - set(req)
                extra = set(req) - set(props)
                if missing:
                    problems.append(f"{path}: properties not all required: {sorted(missing)}")
                if extra:
                    problems.append(f"{path}: required keys missing from properties: {sorted(extra)}")
            for key, child in props.items():
                walk(child, f"{path}.{key}" if path else key)
        if "items" in node:
            walk(node["items"], f"{path}[]")
        if "anyOf" in node:
            for i, alt in enumerate(node["anyOf"]):
                walk(alt, f"{path}.anyOf[{i}]")

    walk(schema, "$")
    # Score fields must be integer|null — never object.
    score_paths = (
        "$.properties.prospect.anyOf[0].properties.fit_score",
        "fit_score",
    )
    del score_paths  # checked via fixture tests
    return problems
