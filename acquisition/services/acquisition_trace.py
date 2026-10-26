"""Developer-only acquisition tracing (ACQUISITION_TRACE).

Silent by default. When enabled, emits compact [ACQ][*] lines via the
standard logging system — never prints secrets, tokens, or full prompts.
Does not alter business logic.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from acquisition.config import Settings, get_settings

logger = logging.getLogger("acquisition.trace")
_FORCE_ON: bool | None = None


def _ensure_trace_logger() -> None:
    """Bind [ACQ] output to the current process stdout.

    Uvicorn often leaves the root logger at WARNING (so logger.info is invisible
    in `docker logs`). Pytest also replaces sys.stdout — so we rebind the
    StreamHandler on each emit to the live stdout stream.
    """
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        try:
            existing.close()
        except Exception:  # noqa: BLE001
            pass
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def set_trace_override(enabled: bool | None) -> None:
    """Process-local override used by the developer CLI. None = follow Settings."""
    global _FORCE_ON
    _FORCE_ON = enabled


def is_acquisition_trace_enabled(settings: Settings | None = None) -> bool:
    if _FORCE_ON is not None:
        return bool(_FORCE_ON)
    env = (os.environ.get("ACQUISITION_TRACE") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    cfg = settings or get_settings()
    return bool(getattr(cfg, "acquisition_trace", False))


def _fmt_list(values: Any, *, limit: int = 12) -> str:
    if values is None:
        return "[]"
    if isinstance(values, str):
        return values
    items = list(values)
    shown = [str(x) for x in items[:limit]]
    if len(items) > limit:
        shown.append(f"…+{len(items) - limit}")
    return "[" + ", ".join(shown) + "]"


def _pct(weight: float) -> str:
    return f"{round(float(weight) * 100):g}%"


def _safe_snippet(text: str | None, *, max_len: int = 160) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1] + "…"


def _emit(section: str, message: str = "", **fields: Any) -> None:
    if not is_acquisition_trace_enabled():
        return
    _ensure_trace_logger()
    prefix = f"[ACQ][{section}]"
    lines: list[str] = []
    if message:
        lines.append(f"{prefix} {message}")
    elif fields:
        lines.append(prefix)
    else:
        lines.append(prefix)
    for key, value in fields.items():
        if value is None:
            rendered = "unknown"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (list, tuple, set)):
            rendered = _fmt_list(value)
        elif isinstance(value, dict):
            # Compact one-line dict — never dump nested secrets.
            parts = []
            for k, v in list(value.items())[:20]:
                parts.append(f"{k}={v}")
            rendered = "{" + ", ".join(parts) + "}"
        else:
            rendered = str(value)
        lines.append(f"{key}={rendered}")
    logger.info("\n".join(lines))


def emit_block(section: str, lines: list[str]) -> None:
    if not is_acquisition_trace_enabled():
        return
    _ensure_trace_logger()
    body = "\n".join(lines)
    logger.info("[ACQ][%s]\n%s", section, body)


def trace_icp(icp: dict[str, Any]) -> None:
    if not is_acquisition_trace_enabled():
        return
    geo = icp.get("geography") or {}
    revenue = icp.get("revenue") or {}
    weights = icp.get("score_weights") or {}
    countries = geo.get("countries") or []
    if geo.get("nationwide") or "United States" in countries:
        geography = "US nationwide"
    else:
        geography = ", ".join(str(c) for c in countries) or "unknown"
    states = geo.get("states") or []
    if states:
        geography = f"{geography}; states={_fmt_list(states)}"
    rmin = revenue.get("primary_min_millions")
    rmax = revenue.get("primary_max_millions")
    smin = revenue.get("sweet_spot_min_millions")
    smax = revenue.get("sweet_spot_max_millions")
    emit_block(
        "ICP",
        [
            f"Geography={geography}",
            f"Revenue=${rmin}M-${rmax}M" if rmin is not None else "Revenue=unknown",
            f"SweetSpot=${smin}M-${smax}M" if smin is not None else "SweetSpot=unknown",
            f"BuyerTitles={_fmt_list(icp.get('buyer_titles'))}",
            f"FitThemes={_fmt_list(icp.get('fit_themes'), limit=6)}",
            f"PriorityCategories={_fmt_list(icp.get('priority_categories'))}",
            f"ExcludedCategories={_fmt_list(icp.get('excluded_categories'))}",
            f"FitWeight={_pct(float(weights.get('fit', 0.45)))}",
            f"TimingWeight={_pct(float(weights.get('timing', 0.25)))}",
            f"ReachabilityWeight={_pct(float(weights.get('reachability', 0.30)))}",
            f"MinPriority={weights.get('ready_for_outreach_min_priority', 55)}",
            f"MinReachability={weights.get('ready_for_outreach_min_reachability', 40)}",
        ],
    )


def trace_query(*, index: int, query: str, reason: str | None = None) -> None:
    if not is_acquisition_trace_enabled():
        return
    _ensure_trace_logger()
    suffix = f" ({reason})" if reason else ""
    logger.info('[ACQ][QUERY] Query %s: "%s"%s', index, query, suffix)


def trace_search(
    *,
    provider: str,
    query: str,
    raw_results: int,
) -> None:
    emit_block(
        "SEARCH",
        [
            f"Provider={provider}",
            f'Query="{query}"',
            f"RawResults={raw_results}",
        ],
    )


def trace_search_result(
    *,
    company: str | None,
    domain: str | None,
    url: str | None,
    title: str | None = None,
    snippet: str | None = None,
) -> None:
    lines = [
        f"Company={company or 'unknown'}",
        f"Domain={domain or 'unknown'}",
        f"URL={url or 'unknown'}",
    ]
    if title:
        lines.append(f"Title={_safe_snippet(title, max_len=120)}")
    if snippet:
        lines.append(f"Snippet={_safe_snippet(snippet, max_len=160)}")
    emit_block("SEARCH][RESULT", lines)


def trace_candidate(
    *,
    company: str,
    decision: str,
    reasons: list[str],
) -> None:
    emit_block(
        "CANDIDATE",
        [
            company or "unknown",
            f"Decision={decision.upper()}",
            f"Reasons={_fmt_list(reasons)}",
        ],
    )


def trace_dedupe(
    *,
    company: str,
    normalized_company: str | None,
    domain: str | None,
    decision_maker: str | None,
    existing_match: bool,
    match_key: str | None,
    action: str,
) -> None:
    emit_block(
        "DEDUPE",
        [
            f'Company="{company}"',
            f'NormalizedCompany="{normalized_company or "unknown"}"',
            f'Domain="{domain or "unknown"}"',
            f'DecisionMaker="{decision_maker or "unknown"}"',
            f"ExistingMatch={'true' if existing_match else 'false'}",
            f"MatchKey={match_key or 'none'}",
            f"Action={action}",
        ],
    )


def trace_save(
    *,
    prospect_id: Any,
    company: str,
    domain: str | None,
    city: str | None,
    state: str | None,
    status: str | None,
    action: str,
) -> None:
    location = ", ".join(p for p in [city, state] if p) or "unknown"
    emit_block(
        "SAVE",
        [
            f"prospect_id={prospect_id}",
            f"company={company}",
            f"domain={domain or 'unknown'}",
            f"location={location}",
            f"status={status or 'unknown'}",
            f"action={action}",
        ],
    )


def trace_research(
    *,
    company: str,
    decision_maker: str | None,
    title: str | None,
    revenue: str | None,
    headcount: str | None,
    city: str | None,
    state: str | None,
    contact_path: str | None,
    timing_signals: list[str],
    sources: int,
    confidence: str | None,
    queries: list[str] | None = None,
    source_urls: list[str] | None = None,
) -> None:
    lines = [
        f"Company={company}",
        f"DecisionMaker={decision_maker or 'unknown'}",
        f"Title={title or 'unknown'}",
        f"RevenueEstimate={revenue or 'unknown'}",
        f"Headcount={headcount or 'unknown'}",
        f"Location={', '.join(p for p in [city, state] if p) or 'unknown'}",
        f"ContactPath={contact_path or 'unknown'}",
        f"TimingSignals={_fmt_list(timing_signals) if timing_signals else '[]'}",
        f"Sources={sources}",
        f"Confidence={confidence or 'unknown'}",
    ]
    if queries:
        for i, q in enumerate(queries[:8], start=1):
            lines.append(f'ResearchQuery{i}="{_safe_snippet(q, max_len=140)}"')
    if source_urls:
        lines.append(f"SourceURLs={_fmt_list(source_urls, limit=8)}")
    emit_block("RESEARCH", lines)


def trace_ai(
    *,
    task: str,
    model: str | None,
    input_summary: str,
    result: dict[str, Any],
) -> None:
    emit_block(
        "AI",
        [
            f"Task={task}",
            f"Model={model or 'unknown'}",
            f'InputSummary="{_safe_snippet(input_summary, max_len=240)}"',
            f"Result={result}",
        ],
    )


def trace_score_fit(*, score: float, reasons: list[str]) -> None:
    emit_block(
        "SCORE][FIT",
        [f"Score={score:g}/100", f"Reasons={_fmt_list(reasons)}"],
    )


def trace_score_timing(*, score: float, reasons: list[str], signals: list[str] | None = None) -> None:
    lines = [f"Score={score:g}/100", f"Reasons={_fmt_list(reasons)}"]
    if signals:
        lines.insert(1, f"TimingSignals={_fmt_list(signals)}")
    emit_block("SCORE][TIMING", lines)


def trace_score_reachability(*, score: float, reasons: list[str]) -> None:
    emit_block(
        "SCORE][REACHABILITY",
        [f"Score={score:g}/100", f"Reasons={_fmt_list(reasons)}"],
    )


def trace_score_priority(
    *,
    fit: float,
    timing: float,
    reach: float,
    w_fit: float,
    w_timing: float,
    w_reach: float,
    base_priority: float,
) -> None:
    emit_block(
        "SCORE][PRIORITY",
        [
            f"Fit={fit:g} x {_pct(w_fit)}",
            f"Timing={timing:g} x {_pct(w_timing)}",
            f"Reachability={reach:g} x {_pct(w_reach)}",
            f"BasePriority={base_priority:g}",
        ],
    )


def trace_ai_learning_skipped(
    *,
    enabled: bool,
    model: str | None,
    historical_sample: int,
    minimum_required: int,
    reason: str,
) -> None:
    emit_block(
        "AI-LEARNING",
        [
            f"Enabled={'true' if enabled else 'false'}",
            f"Model={model or 'unknown'}",
            f"HistoricalSample={historical_sample}",
            f"MinimumRequired={minimum_required}",
            "Status=SKIPPED",
            f'Reason="{reason}"',
        ],
    )


def trace_ai_learning_applied(
    *,
    enabled: bool,
    model: str | None,
    historical_sample: int,
    minimum_required: int,
    comparable_count: int,
    dimensions: dict[str, Any],
    outcomes_summary: dict[str, Any],
    adjustment: float,
    confidence: Any,
    reason: str,
    matched_patterns: list[Any],
    base_priority: float,
    effective_priority: float,
) -> None:
    conf = confidence
    if isinstance(confidence, (int, float)):
        conf = "high" if confidence >= 0.75 else "medium" if confidence >= 0.4 else "low"
    pattern_labels: list[str] = []
    for pat in matched_patterns or []:
        if isinstance(pat, dict):
            dim = pat.get("dimension") or ""
            val = pat.get("value") or ""
            pattern_labels.append(f"{dim}:{val}" if val else str(dim))
        else:
            pattern_labels.append(str(pat))
    emit_block(
        "AI-LEARNING",
        [
            f"Enabled={'true' if enabled else 'false'}",
            f"Model={model or 'unknown'}",
            f"HistoricalSample={historical_sample}",
            f"MinimumRequired={minimum_required}",
            f"ComparableProspects={comparable_count}",
            f"SimilarityDimensions={dimensions}",
            f"OutcomesUsed={outcomes_summary}",
            f"Adjustment={adjustment:+g}",
            f"Confidence={conf}",
            f"MatchedPatterns={_fmt_list(pattern_labels)}",
            f'Reason="{_safe_snippet(reason, max_len=220)}"',
            f"BasePriority={base_priority:g}",
            f"EffectivePriority={effective_priority:g}",
        ],
    )


def trace_done(
    *,
    prospect_id: Any,
    company: str,
    domain: str | None,
    location: str | None,
    discovery_source: str | None,
    fit: Any,
    timing: Any,
    reachability: Any,
    base_priority: Any,
    ai_adjustment: Any,
    effective_priority: Any,
    status: str | None,
) -> None:
    emit_block(
        "DONE",
        [
            f"ProspectID={prospect_id}",
            f"Company={company}",
            f"Domain={domain or 'unknown'}",
            f"Location={location or 'unknown'}",
            f"DiscoverySource={discovery_source or 'unknown'}",
            f"Fit={fit if fit is not None else 'unknown'}",
            f"Timing={timing if timing is not None else 'unknown'}",
            f"Reachability={reachability if reachability is not None else 'unknown'}",
            f"BasePriority={base_priority if base_priority is not None else 'unknown'}",
            f"AIAdjustment={ai_adjustment if ai_adjustment is not None else 'unknown'}",
            f"EffectivePriority={effective_priority if effective_priority is not None else 'unknown'}",
            f"Status={status or 'unknown'}",
        ],
    )


def trace_progress(
    *,
    requested_usable: int,
    candidates_inspected: int,
    usable_found: int,
    decision: str,
    reason: str,
) -> None:
    emit_block(
        "PROGRESS",
        [
            f"RequestedUsable={requested_usable}",
            f"CandidatesInspected={candidates_inspected}",
            f"UsableFound={usable_found}",
            f"Decision={decision.upper()}",
            f'Reason="{reason}"',
        ],
    )


def trace_skip_known_excluded(
    *,
    company: str,
    domain: str | None,
    prospect_id: Any,
    status: str | None,
    reason: str,
) -> None:
    emit_block(
        "SKIP",
        [
            f'Company="{company}"',
            f'Domain="{domain or "unknown"}"',
            f"prospect_id={prospect_id}",
            f"status={status or 'unknown'}",
            f'Reason="{reason}"',
        ],
    )


def diagnose_identity_match(
    *,
    email: str | None,
    website: str | None,
    domain: str | None,
    company: str | None,
    executive_name: str | None,
    existing: Any,
) -> str | None:
    """Explain which identity key likely matched — observational only."""
    if existing is None:
        return None
    from acquisition.services.prospect_identity import (
        normalize_company_name,
        normalize_domain,
        normalize_email,
        normalize_person_name,
        domain_from_website_or_email,
    )

    email_n = normalize_email(email)
    domain_n = normalize_domain(domain) or domain_from_website_or_email(
        website=website, email=email
    )
    company_n = normalize_company_name(company)
    person_n = normalize_person_name(executive_name)
    if email_n and normalize_email(getattr(existing, "email", None)) == email_n:
        return "email"
    if domain_n and normalize_domain(getattr(existing, "domain", None)) == domain_n:
        if person_n and normalize_person_name(getattr(existing, "executive_name", None)) == person_n:
            return "domain+executive_name"
        return "domain"
    if company_n and normalize_company_name(getattr(existing, "company", None)) == company_n:
        if person_n and normalize_person_name(getattr(existing, "executive_name", None)) == person_n:
            return "company+executive_name"
        return "company"
    return "unknown"
