"""Parse and normalize structured agentic discovery / enrichment model output.

Strict typing for Stage B — never crash enrichment with raw TypeError from int()/float().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acquisition.models.prospect import AcquisitionStatus
from acquisition.services.domain_tools import root_domain
from acquisition.services.prospect_identity import normalize_domain

# Field classification for partial-data safety.
CRITICAL_FIELDS = frozenset({"company_name", "website", "normalized_domain"})
IMPORTANT_FIELDS = frozenset({"fit_score", "timing_score", "reachability_score", "contact", "decision_maker"})
OPTIONAL_FIELDS = frozenset(
    {
        "revenue",
        "headcount",
        "timing_signals",
        "research_notes",
        "research_completeness",
        "description",
        "industry",
        "business_type",
        "location",
        "sources",
    }
)


class StructuredOutputValidationError(ValueError):
    """Typed enrichment schema/validation failure (never a raw TypeError)."""

    def __init__(
        self,
        *,
        company: str | None,
        field: str,
        expected: str,
        received_type: str,
        value: Any,
        message: str | None = None,
    ) -> None:
        self.company = company
        self.field = field
        self.expected = expected
        self.received_type = received_type
        self.value = _safe_redact(value)
        detail = message or (
            f"{field}: expected {expected}, got {received_type}"
            + (f" value={self.value!r}" if self.value is not None else "")
        )
        super().__init__(detail)

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "field": self.field,
            "expected": self.expected,
            "received_type": self.received_type,
            "value": self.value,
            "message": str(self),
        }


@dataclass
class EnrichmentParseResult:
    row: dict[str, Any] | None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    critical_failed: bool = False


def _safe_redact(value: Any, *, max_len: int = 120) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_len else value[: max_len - 1] + "…"
    if isinstance(value, list):
        return [_safe_redact(v) for v in value[:5]] + (["…"] if len(value) > 5 else [])
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 8:
                out["…"] = f"+{len(value) - 8} keys"
                break
            out[str(k)] = _safe_redact(v)
        return out
    return str(value)[:max_len]


def normalize_prospect_domain(website: str | None, normalized_domain: str | None) -> str | None:
    raw = (normalized_domain or website or "").strip() or None
    if not raw:
        return None
    return root_domain(raw) or normalize_domain(raw)


def _status_or_unknown(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    if text in {"verified", "supported", "estimated", "unknown"}:
        return text
    return "unknown"


def parse_optional_int(
    value: Any,
    *,
    field: str,
    company: str | None = None,
    allow_null: bool = True,
) -> tuple[int | None, dict[str, Any] | None]:
    """Parse an optional integer. Lists/objects are invalid — never int(list)."""
    if value is None:
        if allow_null:
            return None, None
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer",
            received_type="null",
            value=None,
        )
    if isinstance(value, bool):
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer|null",
            received_type="bool",
            value=value,
        )
    if isinstance(value, int):
        return value, None
    if isinstance(value, float) and value == int(value):
        return int(value), None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"unknown", "n/a", "none", "null"}:
            return None, None
        try:
            return int(float(text)), None
        except ValueError as exc:
            raise StructuredOutputValidationError(
                company=company,
                field=field,
                expected="integer|null",
                received_type="str",
                value=text,
            ) from exc
    if isinstance(value, list):
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer|null",
            received_type="list",
            value=value,
        )
    if isinstance(value, dict):
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer|null",
            received_type="dict",
            value=value,
        )
    raise StructuredOutputValidationError(
        company=company,
        field=field,
        expected="integer|null",
        received_type=type(value).__name__,
        value=value,
    )


def parse_score(
    value: Any,
    *,
    field: str,
    company: str | None = None,
    required: bool = True,
) -> float | None:
    """Scores must be numeric 0..100 or null.

    Also accepts common object shapes the model returns, e.g.
    {"score": 80, "reason": "..."} or {"value": 80}.
    Arrays remain invalid.
    """
    extracted_reason: str | None = None
    if isinstance(value, dict):
        extracted_reason = (
            str(value.get("reason") or value.get("explanation") or "").strip() or None
        )
        nested = None
        for key in (
            "score",
            "value",
            field,
            field.replace("_score", ""),
            "fit",
            "timing",
            "reachability",
            "priority",
        ):
            if key in value and value.get(key) is not None and not isinstance(value.get(key), dict):
                nested = value.get(key)
                break
        if nested is None:
            raise StructuredOutputValidationError(
                company=company,
                field=field,
                expected="integer|null (0..100) or {score, reason}",
                received_type="dict",
                value=value,
                message=f"{field} object missing numeric score",
            )
        value = nested
        # Stash reason for callers that inspect the exception/context via attribute.
        parse_score.last_extracted_reason = extracted_reason  # type: ignore[attr-defined]
    else:
        parse_score.last_extracted_reason = None  # type: ignore[attr-defined]

    if value is None:
        if required:
            raise StructuredOutputValidationError(
                company=company,
                field=field,
                expected="integer|null (0..100)",
                received_type="null",
                value=None,
                message=f"{field} is required for enrichment scoring",
            )
        return None
    if isinstance(value, bool) or isinstance(value, list):
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer|null (0..100)",
            received_type=type(value).__name__,
            value=value,
        )
    if isinstance(value, (int, float)):
        n = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"unknown", "n/a", "none", "null"}:
            if required:
                raise StructuredOutputValidationError(
                    company=company,
                    field=field,
                    expected="integer|null (0..100)",
                    received_type="empty_str",
                    value=text,
                )
            return None
        try:
            n = float(text)
        except ValueError as exc:
            raise StructuredOutputValidationError(
                company=company,
                field=field,
                expected="integer|null (0..100)",
                received_type="str",
                value=text,
            ) from exc
    else:
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer|null (0..100)",
            received_type=type(value).__name__,
            value=value,
        )
    if n != n:  # NaN
        raise StructuredOutputValidationError(
            company=company,
            field=field,
            expected="integer|null (0..100)",
            received_type="nan",
            value=value,
        )
    return max(0.0, min(100.0, n))


parse_score.last_extracted_reason = None  # type: ignore[attr-defined]


def extract_score_and_reason(
    score_value: Any,
    reason_value: Any,
    *,
    field: str,
    company: str | None = None,
    required: bool = True,
) -> tuple[float | None, str]:
    """Parse a score field that may be a bare number or {score, reason} object."""
    score = parse_score(score_value, field=field, company=company, required=required)
    reason = str(reason_value or "").strip()
    if not reason:
        reason = str(getattr(parse_score, "last_extracted_reason", None) or "").strip()
    if not reason and isinstance(score_value, dict):
        reason = str(score_value.get("reason") or score_value.get("explanation") or "").strip()
    return score, reason


# Backward-compatible alias used by older tests/call sites.
def clamp_score(value: Any, default: float = 0.0) -> float:
    """Legacy soft clamp — prefers parse_score for Stage B."""
    try:
        parsed = parse_score(value, field="score", required=False)
        return default if parsed is None else parsed
    except StructuredOutputValidationError:
        return default


def _parse_metric_object(
    raw: Any,
    *,
    field: str,
    company: str | None,
    exact_keys: tuple[str, ...],
    band_keys: tuple[str, ...] = ("band", "value_or_band"),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize revenue/headcount into explicit exact + band objects."""
    warnings: list[dict[str, Any]] = []
    empty = {
        "exact_value": None,
        "exact_amount": None,
        "band": None,
        "value_or_band": None,  # legacy mirror of band/exact for persistence
        "status": "unknown",
        "sources": [],
        "source_urls": [],
    }
    if raw is None:
        return empty, warnings
    if not isinstance(raw, dict):
        warnings.append(
            {
                "field": field,
                "severity": "optional",
                "message": f"{field} expected object, got {type(raw).__name__}; set to unknown",
                "value": _safe_redact(raw),
            }
        )
        return empty, warnings

    exact: int | float | None = None
    for key in exact_keys:
        if key not in raw or raw.get(key) is None:
            continue
        try:
            if field == "revenue":
                # allow float amounts
                val = raw.get(key)
                if isinstance(val, bool) or isinstance(val, (list, dict)):
                    raise StructuredOutputValidationError(
                        company=company,
                        field=f"{field}.{key}",
                        expected="number|null",
                        received_type=type(val).__name__,
                        value=val,
                    )
                if isinstance(val, (int, float)):
                    exact = float(val)
                elif isinstance(val, str) and val.strip():
                    exact = float(val.strip())
                else:
                    exact = None
            else:
                exact, _ = parse_optional_int(raw.get(key), field=f"{field}.{key}", company=company)
            break
        except (StructuredOutputValidationError, ValueError) as exc:
            warnings.append(
                {
                    "field": f"{field}.{key}",
                    "severity": "optional",
                    "message": str(exc),
                    "value": _safe_redact(raw.get(key)),
                }
            )
            exact = None

    band = None
    for key in band_keys:
        b = raw.get(key)
        if b is None:
            continue
        if isinstance(b, (list, dict)):
            warnings.append(
                {
                    "field": f"{field}.{key}",
                    "severity": "optional",
                    "message": f"{field}.{key} expected string|null, got {type(b).__name__}",
                    "value": _safe_redact(b),
                }
            )
            continue
        text = str(b).strip()
        if text and text.lower() not in {"unknown", "n/a", "none", "null"}:
            band = text
            break

    # Legacy single value_or_band that was an int — promote to exact.
    if exact is None and band is None and "value_or_band" in raw:
        v = raw.get("value_or_band")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            exact = float(v) if field == "revenue" else int(v)
        elif isinstance(v, str) and v.strip():
            band = v.strip()

    sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
    source_urls = raw.get("source_urls") if isinstance(raw.get("source_urls"), list) else []
    url_list = []
    for item in list(sources) + list(source_urls):
        if isinstance(item, dict) and item.get("url"):
            url_list.append(str(item["url"]))
        elif isinstance(item, str) and item.strip():
            url_list.append(item.strip())

    status = _status_or_unknown(raw.get("status"))
    if exact is None and band is None:
        status = "unknown"

    legacy_value = band if band is not None else (str(int(exact)) if isinstance(exact, int) else (str(exact) if exact is not None else None))

    return {
        "exact_value": int(exact) if field == "headcount" and exact is not None else (exact if field == "headcount" else None),
        "exact_amount": float(exact) if field == "revenue" and exact is not None else None,
        "band": band,
        "value_or_band": legacy_value,
        "status": status,
        "sources": [{"url": u} for u in url_list],
        "source_urls": url_list,
    }, warnings


def _parse_source_urls_consulted(
    value: Any,
    *,
    sources: list[Any],
    company: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    """Optional completeness count — lists are treated as URL lists (len), not int(list)."""
    warnings: list[dict[str, Any]] = []
    fallback = len([s for s in sources if isinstance(s, dict) and s.get("url")])
    if value is None:
        return fallback, warnings
    if isinstance(value, bool):
        warnings.append(
            {
                "field": "research_completeness.source_urls_consulted",
                "severity": "optional",
                "message": "bool not allowed; using source count",
                "value": value,
            }
        )
        return fallback, warnings
    if isinstance(value, int):
        return max(0, value), warnings
    if isinstance(value, float) and value == int(value):
        return max(0, int(value)), warnings
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback, warnings
        try:
            return max(0, int(float(text))), warnings
        except ValueError:
            warnings.append(
                {
                    "field": "research_completeness.source_urls_consulted",
                    "severity": "optional",
                    "message": "non-numeric string; using source count",
                    "value": _safe_redact(text),
                }
            )
            return fallback, warnings
    if isinstance(value, list):
        # Model often returns the URL list itself under this key name.
        warnings.append(
            {
                "field": "research_completeness.source_urls_consulted",
                "severity": "optional",
                "message": "received list (likely URLs); using len(list) instead of int(list)",
                "value": _safe_redact(value),
                "company": company,
            }
        )
        return max(len(value), fallback), warnings
    if isinstance(value, dict):
        warnings.append(
            {
                "field": "research_completeness.source_urls_consulted",
                "severity": "optional",
                "message": "received object; using source count",
                "value": _safe_redact(value),
            }
        )
        return fallback, warnings
    warnings.append(
        {
            "field": "research_completeness.source_urls_consulted",
            "severity": "optional",
            "message": f"unexpected type {type(value).__name__}; using source count",
            "value": _safe_redact(value),
        }
    )
    return fallback, warnings


def parse_discovery_candidates(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Stage A — strong provisional seeds (identity + preliminary readiness evidence)."""
    return parse_discovery_result(payload)["prospects"]


def parse_discovery_result(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Parse Stage A master-discovery payload into prospects + optional discovery_meta."""
    empty: dict[str, Any] = {
        "prospects": [],
        "discovery_meta": {
            "companies_considered": None,
            "rejected_before_selection": [],
            "rejection_reason_counts": {},
        },
    }
    if not isinstance(payload, dict):
        return empty
    rows = payload.get("prospects") or payload.get("candidates") or []
    if not isinstance(rows, list):
        rows = []
    out: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        company = str(raw.get("company_name") or raw.get("company") or "").strip()
        if not company:
            continue
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        website = (
            (raw.get("official_website") or raw.get("website") or "").strip() or None
        )
        domain = normalize_prospect_domain(website, raw.get("normalized_domain"))
        sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
        evidence = raw.get("initial_evidence_urls") if isinstance(raw.get("initial_evidence_urls"), list) else []
        # Pull URLs from sources when initial_evidence_urls omitted.
        if not evidence:
            evidence = [
                str(s.get("url")).strip()
                for s in sources
                if isinstance(s, dict) and s.get("url")
            ]
        scale = (
            raw.get("preliminary_scale_evidence")
            if isinstance(raw.get("preliminary_scale_evidence"), dict)
            else {}
        )
        dm = (
            raw.get("preliminary_decision_maker")
            if isinstance(raw.get("preliminary_decision_maker"), dict)
            else {}
        )
        contactability = (
            raw.get("preliminary_contactability")
            if isinstance(raw.get("preliminary_contactability"), dict)
            else {}
        )
        timing = raw.get("timing_signals") if isinstance(raw.get("timing_signals"), list) else []
        out.append(
            {
                "company_name": company,
                "website": website,
                "official_website": website,
                "normalized_domain": domain,
                "location": {
                    "city": (location.get("city") or raw.get("city") or None),
                    "state": (location.get("state") or raw.get("state") or None),
                    "country": (location.get("country") or "US"),
                },
                "industry": raw.get("industry"),
                "business_type": raw.get("business_type") or raw.get("industry"),
                "description": raw.get("description"),
                "sources": [s for s in sources if isinstance(s, dict) and s.get("url")],
                "initial_evidence_urls": [str(u).strip() for u in evidence if str(u).strip()],
                "preliminary_scale_evidence": scale or None,
                "preliminary_decision_maker": dm or None,
                "preliminary_contactability": contactability or None,
                "timing_signals": timing,
                "why_strong_seed": raw.get("why_strong_seed") or raw.get("why_selected"),
                "raw": raw,
            }
        )

    meta_raw = payload.get("discovery_meta") if isinstance(payload.get("discovery_meta"), dict) else {}
    rejected: list[dict[str, Any]] = []
    for item in meta_raw.get("rejected_before_selection") or []:
        if not isinstance(item, dict):
            continue
        rejected.append(
            {
                "company_name": item.get("company_name") or item.get("company"),
                "domain": item.get("domain") or item.get("normalized_domain"),
                "reason_code": item.get("reason_code") or "rejected",
                "reason": item.get("reason") or item.get("reason_text"),
            }
        )
    counts_raw = meta_raw.get("rejection_reason_counts")
    counts: dict[str, int] = {}
    if isinstance(counts_raw, dict):
        for k, v in counts_raw.items():
            try:
                counts[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    elif isinstance(counts_raw, list):
        for item in counts_raw:
            if not isinstance(item, dict):
                continue
            code = str(item.get("reason_code") or item.get("code") or "").strip()
            if not code:
                continue
            try:
                counts[code] = counts.get(code, 0) + int(item.get("count") or 0)
            except (TypeError, ValueError):
                continue
    # Derive counts from rejected list when AI omitted tallies.
    if not counts and rejected:
        for item in rejected:
            code = str(item.get("reason_code") or "rejected")
            counts[code] = counts.get(code, 0) + 1

    considered = meta_raw.get("companies_considered")
    companies_considered: int | None
    try:
        companies_considered = int(considered) if considered is not None else None
    except (TypeError, ValueError):
        companies_considered = None
    # Honest lower bound only when the model reported structured rejects.
    if companies_considered is None and rejected:
        companies_considered = len(out) + len(rejected)

    return {
        "prospects": out,
        "discovery_meta": {
            "companies_considered": companies_considered,
            "rejected_before_selection": rejected,
            "rejection_reason_counts": counts,
            "strong_seeds_returned": len(out),
        },
    }


def parse_enrichment_result(
    payload: dict[str, Any] | None,
    *,
    company_hint: str | None = None,
) -> EnrichmentParseResult:
    """Strict Stage B parse. Optional field failures → warnings; critical/score failures → errors."""
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if not isinstance(payload, dict):
        return EnrichmentParseResult(
            row=None,
            errors=[
                {
                    "field": "payload",
                    "expected": "object",
                    "received_type": type(payload).__name__,
                    "message": "enrichment payload is not an object",
                }
            ],
            critical_failed=True,
        )

    # Single prospect or {"prospects":[...]}
    if "company_name" in payload or "company" in payload:
        raw = payload
    else:
        rows = payload.get("prospects")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return EnrichmentParseResult(
                row=None,
                errors=[{"field": "prospects", "message": "no prospect object in enrichment payload"}],
                critical_failed=True,
            )
        raw = rows[0]

    company = str(raw.get("company_name") or raw.get("company") or company_hint or "").strip()
    if not company:
        return EnrichmentParseResult(
            row=None,
            errors=[{"field": "company_name", "message": "missing company_name"}],
            critical_failed=True,
        )

    website = (raw.get("website") or "").strip() or None
    domain = normalize_prospect_domain(website, raw.get("normalized_domain"))
    if not domain and not website:
        return EnrichmentParseResult(
            row=None,
            errors=[
                {
                    "field": "normalized_domain",
                    "message": "missing website/domain (critical identity)",
                    "company": company,
                }
            ],
            critical_failed=True,
        )

    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    if raw.get("location") is not None and not isinstance(raw.get("location"), dict):
        warnings.append(
            {
                "field": "location",
                "severity": "optional",
                "message": f"location expected object, got {type(raw.get('location')).__name__}",
            }
        )

    revenue, rev_warn = _parse_metric_object(
        raw.get("revenue"),
        field="revenue",
        company=company,
        exact_keys=("exact_amount", "exact_value", "amount"),
    )
    warnings.extend(rev_warn)
    headcount, hc_warn = _parse_metric_object(
        raw.get("headcount"),
        field="headcount",
        company=company,
        exact_keys=("exact_value", "exact_amount", "count"),
    )
    # headcount exact is int
    if headcount.get("exact_amount") is not None and headcount.get("exact_value") is None:
        try:
            headcount["exact_value"] = int(headcount["exact_amount"])
        except (TypeError, ValueError):
            headcount["exact_value"] = None
    headcount["exact_amount"] = None
    warnings.extend(hc_warn)

    dm = raw.get("decision_maker") if isinstance(raw.get("decision_maker"), dict) else {}
    if raw.get("decision_maker") is not None and not isinstance(raw.get("decision_maker"), dict):
        warnings.append(
            {
                "field": "decision_maker",
                "severity": "important",
                "message": f"decision_maker expected object, got {type(raw.get('decision_maker')).__name__}",
            }
        )
        dm = {}
    contact = raw.get("contact") if isinstance(raw.get("contact"), dict) else {}
    if raw.get("contact") is not None and not isinstance(raw.get("contact"), dict):
        warnings.append(
            {
                "field": "contact",
                "severity": "important",
                "message": f"contact expected object, got {type(raw.get('contact')).__name__}",
            }
        )
        contact = {}

    completeness = (
        raw.get("research_completeness")
        if isinstance(raw.get("research_completeness"), dict)
        else {}
    )
    sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
    signals = raw.get("timing_signals") if isinstance(raw.get("timing_signals"), list) else []
    if raw.get("timing_signals") is not None and not isinstance(raw.get("timing_signals"), list):
        warnings.append(
            {
                "field": "timing_signals",
                "severity": "optional",
                "message": f"timing_signals expected array, got {type(raw.get('timing_signals')).__name__}",
            }
        )
        signals = []

    dm_name = (dm.get("name") or "").strip() or None
    if dm_name and dm_name.lower() in {"unknown", "n/a", "none", "null"}:
        dm_name = None

    consulted, consulted_warn = _parse_source_urls_consulted(
        completeness.get("source_urls_consulted"),
        sources=sources,
        company=company,
    )
    warnings.extend(consulted_warn)

    # Important: scores — hard validation (triggers repair retry upstream).
    # Accept bare ints OR {score, reason} objects the model commonly returns.
    try:
        fit_score, fit_reason = extract_score_and_reason(
            raw.get("fit_score") if raw.get("fit_score") is not None else raw.get("fit"),
            raw.get("fit_reason"),
            field="fit_score",
            company=company,
            required=True,
        )
        timing_score, timing_reason = extract_score_and_reason(
            raw.get("timing_score") if raw.get("timing_score") is not None else raw.get("timing"),
            raw.get("timing_reason"),
            field="timing_score",
            company=company,
            required=True,
        )
        reachability_score, reachability_reason = extract_score_and_reason(
            raw.get("reachability_score")
            if raw.get("reachability_score") is not None
            else raw.get("reachability"),
            raw.get("reachability_reason"),
            field="reachability_score",
            company=company,
            required=True,
        )
    except StructuredOutputValidationError as exc:
        errors.append(exc.as_dict())
        return EnrichmentParseResult(row=None, warnings=warnings, errors=errors, critical_failed=False)

    row = {
        "company_name": company,
        "website": website,
        "normalized_domain": domain,
        "company_identity_verified": bool(domain),
        "location": {
            "city": (location.get("city") or raw.get("city") or None),
            "state": (location.get("state") or raw.get("state") or None),
            "country": (location.get("country") or "US"),
        },
        "industry": raw.get("industry") if not isinstance(raw.get("industry"), (list, dict)) else None,
        "business_type": (
            raw.get("business_type") or raw.get("industry")
            if not isinstance(raw.get("business_type"), (list, dict))
            else None
        ),
        "description": raw.get("description") if isinstance(raw.get("description"), str) or raw.get("description") is None else str(raw.get("description")),
        "revenue": revenue,
        "headcount": headcount,
        "decision_maker": {
            "name": dm_name,
            "title": dm.get("title"),
            "relationship": dm.get("relationship"),
            "authority_level": dm.get("authority_level"),
            "profile_url": dm.get("profile_url"),
            "source_urls": list(dm.get("source_urls") or []) if isinstance(dm.get("source_urls"), list) else [],
            "sources": list(dm.get("sources") or []) if isinstance(dm.get("sources"), list) else [],
            "confidence": (dm.get("confidence") or "unknown"),
            "status": _status_or_unknown(dm.get("status") or ("verified" if dm_name else "unknown")),
            "search_attempted": bool(
                dm.get("search_attempted") if dm.get("search_attempted") is not None else True
            ),
        },
        "contact": {
            "business_email": contact.get("business_email"),
            "email_status": contact.get("email_status") or "unknown",
            "phone": contact.get("phone"),
            "contact_url": contact.get("contact_url"),
            "source_urls": list(contact.get("source_urls") or [])
            if isinstance(contact.get("source_urls"), list)
            else [],
            "sources": list(contact.get("sources") or []) if isinstance(contact.get("sources"), list) else [],
            "status": _status_or_unknown(contact.get("status")),
        },
        "timing_signals": [
            s for s in signals if isinstance(s, dict) and (s.get("signal") or s.get("text"))
        ],
        "research_notes": raw.get("research_notes"),
        "research_completeness": {
            "company_identity": _status_or_unknown(completeness.get("company_identity")),
            "website": _status_or_unknown(completeness.get("website")),
            "location": _status_or_unknown(completeness.get("location")),
            "industry": _status_or_unknown(completeness.get("industry")),
            "revenue": _status_or_unknown(completeness.get("revenue") or revenue.get("status")),
            "headcount": _status_or_unknown(completeness.get("headcount") or headcount.get("status")),
            "decision_maker": _status_or_unknown(
                completeness.get("decision_maker") or ("verified" if dm_name else "unknown")
            ),
            "contact": _status_or_unknown(completeness.get("contact")),
            "timing_signals": _status_or_unknown(completeness.get("timing_signals")),
            "source_urls_consulted": consulted,
            "overall": _status_or_unknown(completeness.get("overall")),
        },
        "fit_score": fit_score,
        "fit_reason": fit_reason,
        "timing_score": timing_score,
        "timing_reason": timing_reason,
        "reachability_score": reachability_score,
        "reachability_reason": reachability_reason,
        "sources": [s for s in sources if isinstance(s, dict) and s.get("url")],
        "validation_warnings": warnings,
        "raw": raw,
    }
    return EnrichmentParseResult(row=row, warnings=warnings, errors=errors, critical_failed=False)


def parse_agentic_prospects(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Full prospect rows (Stage B enrichment, or legacy one-shot schema)."""
    result = parse_enrichment_result(payload)
    if result.row:
        return [result.row]
    # Legacy multi-prospect payloads for older one-shot tests.
    if isinstance(payload, dict) and isinstance(payload.get("prospects"), list):
        out: list[dict[str, Any]] = []
        for raw in payload["prospects"]:
            if not isinstance(raw, dict):
                continue
            parsed = parse_enrichment_result(raw)
            if parsed.row:
                out.append(parsed.row)
        return out
    return []


def has_legitimate_contact_path(prospect_row: dict[str, Any]) -> bool:
    contact = prospect_row.get("contact") or {}
    email = (contact.get("business_email") or "").strip()
    email_status = (contact.get("email_status") or "").strip().lower()
    if email and email_status not in {"unknown", "none", "missing", "placeholder"}:
        if "example.com" not in email.lower() and "@" in email:
            return True
    if email and "@" in email and "example.com" not in email.lower() and email_status != "placeholder":
        return True
    if (contact.get("phone") or "").strip():
        return True
    if (contact.get("contact_url") or "").strip():
        return True
    return False


def has_verified_business_email(prospect_row: dict[str, Any]) -> bool:
    contact = prospect_row.get("contact") or {}
    email = (contact.get("business_email") or "").strip()
    email_status = (contact.get("email_status") or "").strip().lower()
    if not email or "@" not in email or "example.com" in email.lower():
        return False
    if email_status in {"unknown", "none", "missing", "placeholder", "guessed", "pattern"}:
        return False
    return True


def has_decision_maker(prospect_row: dict[str, Any]) -> bool:
    dm = prospect_row.get("decision_maker") or {}
    name = (dm.get("name") or "").strip()
    if not name:
        return False
    if name.lower() in {"unknown", "unknown decision-maker", "n/a", "none", "null"}:
        return False
    return True


def is_usable_prospect(
    enriched_prospect: dict[str, Any] | None,
    admin_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Central usable-prospect gate for final requested_count filling."""
    admin = admin_settings or {}
    if not enriched_prospect:
        return {
            "usable": False,
            "reason_code": "empty_enrichment",
            "reason_text": "No enrichment result",
        }
    company = (enriched_prospect.get("company_name") or "").strip()
    domain = (enriched_prospect.get("normalized_domain") or "").strip()
    website = (enriched_prospect.get("website") or "").strip()
    if not company or (not domain and not website):
        return {
            "usable": False,
            "reason_code": "missing_identity",
            "reason_text": "Missing company identity or website/domain",
        }
    excluded = [str(x) for x in (admin.get("excluded_categories") or [])]
    hit = matches_exclusion(enriched_prospect, excluded)
    if hit:
        return {
            "usable": False,
            "reason_code": "excluded",
            "reason_text": f"Matches exclusion: {hit}",
        }

    fit = enriched_prospect.get("fit_score")
    timing = enriched_prospect.get("timing_score")
    reach = enriched_prospect.get("reachability_score")
    if fit is None or timing is None or reach is None:
        return {
            "usable": False,
            "reason_code": "missing_scores",
            "reason_text": "Component scores missing after enrichment",
        }

    contact_ok = has_legitimate_contact_path(enriched_prospect)
    dm_ok = has_decision_maker(enriched_prospect)
    if not contact_ok and not dm_ok:
        return {
            "usable": False,
            "reason_code": "missing_contact_path",
            "reason_text": (
                "No legitimate business contact path and no identified decision-maker"
            ),
        }

    # Credible ICP fit floor — below this, needs more research (not usable toward quota).
    if float(fit) < 35:
        return {
            "usable": False,
            "reason_code": "needs_research",
            "reason_text": f"Fit score {float(fit):.0f} below research confidence floor",
        }

    return {
        "usable": True,
        "reason_code": "usable",
        "reason_text": (
            "Credible company research with component scores and a legitimate "
            "contact path or decision-maker"
        ),
    }


def evaluate_readiness(
    *,
    priority: float,
    fit: float,
    reach: float,
    prospect_row: dict[str, Any],
    ready_min_priority: float,
    ready_min_reach: float,
    phone: str | None = None,
    contact_url: str | None = None,
) -> tuple[AcquisitionStatus, str]:
    """Backend-authoritative readiness. Never READY on scores alone without contact evidence."""
    contact_ok = has_legitimate_contact_path(prospect_row)
    if not contact_ok:
        contact_ok = bool((phone or "").strip() or (contact_url or "").strip())
    dm_ok = has_decision_maker(prospect_row)
    email_ok = has_verified_business_email(prospect_row)
    # Policy: READY needs score thresholds AND (identified DM OR verified business email).
    # Phone/contact-page alone is a contact path but not enough for READY without DM/email.
    outreach_identity_ok = dm_ok or email_ok

    if reach < 20 or not contact_ok:
        return (
            AcquisitionStatus.UNREACHABLE,
            "No legitimate outreach contact path (business email, phone, or contact page).",
        )

    if (
        priority >= ready_min_priority
        and reach >= ready_min_reach
        and contact_ok
        and outreach_identity_ok
    ):
        if dm_ok and email_ok:
            reason = (
                f"Priority {priority:.0f} and reachability {reach:.0f} meet thresholds; "
                "decision-maker identified and verified business email present."
            )
        elif dm_ok:
            reason = (
                f"Priority {priority:.0f} and reachability {reach:.0f} meet thresholds; "
                "decision-maker identified with a legitimate contact path."
            )
        else:
            reason = (
                f"Priority {priority:.0f} and reachability {reach:.0f} meet thresholds; "
                "decision-maker unknown but verified business email contact path is sufficient."
            )
        return AcquisitionStatus.READY_FOR_OUTREACH, reason

    if not outreach_identity_ok and priority >= ready_min_priority and reach >= ready_min_reach:
        return (
            AcquisitionStatus.SCORED,
            (
                f"Priority {priority:.0f} / reachability {reach:.0f} pass numeric thresholds, "
                "but decision-maker is unknown and no verified business email was found — "
                "not ready for outreach."
            ),
        )

    if fit < 35:
        return (
            AcquisitionStatus.NEEDS_MORE_RESEARCH,
            f"Fit score {fit:.0f} is below research confidence floor; needs more research.",
        )

    parts = []
    if priority < ready_min_priority:
        parts.append(f"priority {priority:.0f} < {ready_min_priority:.0f}")
    if reach < ready_min_reach:
        parts.append(f"reachability {reach:.0f} < {ready_min_reach:.0f}")
    if not outreach_identity_ok:
        parts.append("decision-maker unknown and no verified business email")
    return (
        AcquisitionStatus.SCORED,
        "Scored but not ready: " + ("; ".join(parts) if parts else "thresholds not met"),
    )


def matches_exclusion(prospect_row: dict[str, Any], excluded: list[str]) -> str | None:
    blob = " ".join(
        str(x)
        for x in [
            prospect_row.get("company_name"),
            prospect_row.get("industry"),
            prospect_row.get("business_type"),
            prospect_row.get("description"),
        ]
        if x
    ).lower()
    for ex in excluded:
        e = (ex or "").strip().lower()
        if not e:
            continue
        if e in blob:
            return ex
    return None


def is_generic_inbox(email: str | None) -> bool:
    """True for common non-executive shared inboxes (info@, hello@, etc.)."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].strip().lower()
    return local in {
        "info",
        "hello",
        "contact",
        "admin",
        "support",
        "sales",
        "office",
        "team",
        "mail",
        "enquiries",
        "inquiry",
        "inquiries",
        "help",
        "service",
        "customerservice",
        "customer.service",
        "general",
        "reception",
    }


def evaluate_outreach_ready(
    prospect_row: dict[str, Any] | None,
    admin_settings: dict[str, Any] | None = None,
    *,
    priority: float | None = None,
    fit_w: float | None = None,
    timing_w: float | None = None,
    reach_w: float | None = None,
) -> dict[str, Any]:
    """Backend-authoritative outreach-ready gate (stricter than is_usable_prospect).

    For email channel (default): requires verified business email + decision-maker,
    score thresholds, ICP/exclusion checks. AI recommendation is advisory only.
    """
    admin = admin_settings or {}
    weights = admin.get("score_weights") or {}
    channel = str(weights.get("outreach_channel") or admin.get("outreach_channel") or "email").lower()
    allow_generic = bool(weights.get("allow_generic_inbox", False))
    ready_min_priority = float(weights.get("ready_for_outreach_min_priority", 55))
    ready_min_reach = float(weights.get("ready_for_outreach_min_reachability", 40))

    if not prospect_row:
        return {
            "ready": False,
            "reason_code": "empty",
            "reason_text": "No prospect payload",
            "checks": {},
        }

    company = (prospect_row.get("company_name") or "").strip()
    domain = (prospect_row.get("normalized_domain") or "").strip()
    website = (prospect_row.get("website") or "").strip()
    checks: dict[str, bool] = {
        "company_verified": bool(company and (domain or website)),
        "icp_verified": True,
        "scale_supported": True,
        "decision_maker_verified": has_decision_maker(prospect_row),
        "contact_path_verified": False,
        "personalization_context_available": bool(
            (prospect_row.get("description") or "").strip()
            or (prospect_row.get("research_notes") or "").strip()
            or (prospect_row.get("timing_signals") or [])
        ),
        "scores_present": all(
            prospect_row.get(k) is not None
            for k in ("fit_score", "timing_score", "reachability_score")
        ),
        "exclusion_clear": True,
        "email_ok": False,
        "generic_inbox_ok": True,
    }

    if not checks["company_verified"]:
        return {
            "ready": False,
            "reason_code": "missing_identity",
            "reason_text": "Missing verified company identity/website",
            "checks": checks,
            "channel": channel,
        }

    hit = matches_exclusion(prospect_row, [str(x) for x in (admin.get("excluded_categories") or [])])
    if hit:
        checks["exclusion_clear"] = False
        checks["icp_verified"] = False
        return {
            "ready": False,
            "reason_code": "excluded",
            "reason_text": f"Matches exclusion: {hit}",
            "checks": checks,
            "channel": channel,
        }

    if not checks["scores_present"]:
        return {
            "ready": False,
            "reason_code": "missing_scores",
            "reason_text": "Component scores missing",
            "checks": checks,
            "channel": channel,
        }

    fit = float(prospect_row["fit_score"])
    timing = float(prospect_row["timing_score"])
    reach = float(prospect_row["reachability_score"])
    if fit_w is None or timing_w is None or reach_w is None:
        from acquisition.services.priority_score import scoring_weights_as_percent

        fit_w, timing_w, reach_w = scoring_weights_as_percent(weights)
    if priority is None:
        from acquisition.services.priority_score import calculate_priority_score

        priority = calculate_priority_score(fit, timing, reach, fit_w, timing_w, reach_w)

    email_ok = has_verified_business_email(prospect_row)
    contact = prospect_row.get("contact") or {}
    email = (contact.get("business_email") or "").strip()
    checks["email_ok"] = email_ok
    if email_ok and is_generic_inbox(email) and not allow_generic:
        checks["generic_inbox_ok"] = False
        checks["email_ok"] = False
        email_ok = False

    if channel == "email":
        checks["contact_path_verified"] = bool(email_ok)
        if not email_ok:
            return {
                "ready": False,
                "reason_code": "missing_required_contact",
                "reason_text": (
                    "Email outreach requires a verified non-pattern business email"
                    + (" (generic inboxes not allowed by Admin policy)" if not checks["generic_inbox_ok"] else "")
                ),
                "checks": checks,
                "channel": channel,
                "priority": priority,
            }
    else:
        checks["contact_path_verified"] = has_legitimate_contact_path(prospect_row)

    if not checks["decision_maker_verified"]:
        return {
            "ready": False,
            "reason_code": "missing_decision_maker",
            "reason_text": "Decision-maker not verified",
            "checks": checks,
            "channel": channel,
            "priority": priority,
        }

    if not checks["personalization_context_available"]:
        return {
            "ready": False,
            "reason_code": "insufficient_context",
            "reason_text": "Not enough researched context for responsible outreach",
            "checks": checks,
            "channel": channel,
            "priority": priority,
        }

    if fit < 35:
        checks["icp_verified"] = False
        return {
            "ready": False,
            "reason_code": "needs_research",
            "reason_text": f"Fit score {fit:.0f} below confidence floor",
            "checks": checks,
            "channel": channel,
            "priority": priority,
        }

    if priority < ready_min_priority or reach < ready_min_reach:
        return {
            "ready": False,
            "reason_code": "below_thresholds",
            "reason_text": (
                f"Priority {priority:.0f} / reachability {reach:.0f} below Admin ready thresholds "
                f"({ready_min_priority:.0f}/{ready_min_reach:.0f})"
            ),
            "checks": checks,
            "channel": channel,
            "priority": priority,
        }

    # Scale: revenue unknown is OK when Admin allows outside band / other evidence exists.
    revenue = prospect_row.get("revenue") or {}
    headcount = prospect_row.get("headcount") or {}
    scale_ok = bool(
        revenue.get("value_or_band")
        or revenue.get("exact_amount") is not None
        or revenue.get("band")
        or headcount.get("value_or_band")
        or headcount.get("exact_value") is not None
        or headcount.get("band")
        or (admin.get("revenue") or {}).get("allow_strong_fit_outside_band")
        or fit >= 70
    )
    checks["scale_supported"] = scale_ok
    if not scale_ok:
        return {
            "ready": False,
            "reason_code": "scale_unsupported",
            "reason_text": "Insufficient scale evidence under Admin rules",
            "checks": checks,
            "channel": channel,
            "priority": priority,
        }

    return {
        "ready": True,
        "reason_code": "ready_for_outreach",
        "reason_text": (
            f"Backend-validated ready for {channel}: DM + contact + scores "
            f"(priority {priority:.0f}, reach {reach:.0f})"
        ),
        "checks": checks,
        "channel": channel,
        "priority": priority,
        "mandatory_requirements_passed": True,
    }


def parse_slot_result(
    payload: dict[str, Any] | None,
    *,
    slot_id: str,
    company_hint: str | None = None,
) -> dict[str, Any]:
    """Parse an outreach-slot agent response into a normalized slot result."""
    if not isinstance(payload, dict):
        return {
            "slot_id": slot_id,
            "status": "technical_failure",
            "candidates_attempted": 0,
            "rejected_candidates": [],
            "prospect": None,
            "failure_reason": "slot_payload_not_object",
            "parse_errors": [{"field": "payload", "message": "not an object"}],
        }

    status = str(payload.get("status") or "").strip().lower()
    if status not in {"ready", "exhausted", "technical_failure"}:
        # Legacy: single enriched prospect object without slot wrapper.
        if payload.get("company_name") or payload.get("company") or payload.get("prospects"):
            parsed = parse_enrichment_result(payload, company_hint=company_hint)
            if parsed.row:
                return {
                    "slot_id": slot_id,
                    "status": "ready",
                    "candidates_attempted": 1,
                    "rejected_candidates": [],
                    "prospect": parsed.row,
                    "failure_reason": None,
                    "ai_recommended_ready": True,
                    "validation_warnings": parsed.warnings,
                }
            return {
                "slot_id": slot_id,
                "status": "technical_failure",
                "candidates_attempted": 1,
                "rejected_candidates": [],
                "prospect": None,
                "failure_reason": "schema_invalid",
                "parse_errors": parsed.errors,
            }
        status = "exhausted"

    rejected_raw = payload.get("rejected_candidates") if isinstance(payload.get("rejected_candidates"), list) else []
    rejected: list[dict[str, Any]] = []
    for item in rejected_raw:
        if not isinstance(item, dict):
            continue
        rejected.append(
            {
                "company_name": item.get("company_name") or item.get("company"),
                "domain": item.get("domain") or item.get("normalized_domain"),
                "reason_code": item.get("reason_code") or "rejected",
                "reason": item.get("reason") or item.get("reason_text"),
            }
        )

    attempts = payload.get("candidates_attempted")
    try:
        candidates_attempted = int(attempts) if attempts is not None else max(1, len(rejected) + (1 if payload.get("prospect") else 0))
    except (TypeError, ValueError):
        candidates_attempted = max(1, len(rejected))

    prospect_raw = payload.get("prospect")
    prospect = None
    parse_errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if isinstance(prospect_raw, dict):
        parsed = parse_enrichment_result(prospect_raw, company_hint=company_hint)
        warnings = parsed.warnings
        if parsed.row:
            readiness = prospect_raw.get("outreach_readiness")
            if isinstance(readiness, dict):
                parsed.row["outreach_readiness"] = readiness
            prospect = parsed.row
        else:
            parse_errors = parsed.errors
            if status == "ready":
                status = "technical_failure"

    ai_ready = False
    if isinstance(prospect_raw, dict):
        orr = prospect_raw.get("outreach_readiness") if isinstance(prospect_raw.get("outreach_readiness"), dict) else {}
        ai_ready = bool(orr.get("ai_recommended_ready") or orr.get("mandatory_requirements_passed"))
    if status == "ready" and prospect is not None:
        ai_ready = True

    return {
        "slot_id": slot_id,
        "status": status,
        "candidates_attempted": candidates_attempted,
        "rejected_candidates": rejected,
        "prospect": prospect,
        "failure_reason": payload.get("failure_reason"),
        "ai_recommended_ready": ai_ready,
        "validation_warnings": warnings,
        "parse_errors": parse_errors,
    }


def is_transient_enrichment_error(error: str | None) -> bool:
    if not error:
        return False
    text = error.lower()
    if text in {"empty_result", "openai_discovery_malformed_output", "openai_discovery_empty"}:
        return True
    if text.startswith("http_5") or text.startswith("http_429"):
        return True
    if text.startswith("openai_error:"):
        return True
    if any(tok in text for tok in ("timeout", "timed out", "transport", "connection", "temporarily")):
        return True
    return False
