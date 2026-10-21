"""Pre-research AI candidate triage — global comparison of the full surviving pool.

Compares ALL surviving candidates against the FULL ICP and selects which ones
deserve expensive deep research. This is NOT Fit/Timing/Reachability scoring.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.types import DiscoveryCandidate
from acquisition.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

PROMPT_VERSION = "discovery_candidate_triage_v2"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
# Soft char budget for the user message (candidates are truncated to fit).
_PAYLOAD_SOFT_CHAR_BUDGET = 180_000
_DESC_MAX_DEFAULT = 400
_DESC_MAX_COMPACT = 180

_SYSTEM_PROMPT = """You select discovery candidates for expensive deep research.

You receive the FULL Ideal Customer Profile (ICP) and EVERY surviving candidate
from the discovery pool. Your job is ONE global comparison:

"From this complete candidate pool, select the companies that most deserve
deeper research based on the configured ICP and the evidence currently available."

Rules:
- Return ONLY valid JSON matching the required schema.
- Select exactly requested_count companies when enough candidates exist.
- If fewer candidates than requested_count, select all of them.
- Use only evidence present on each candidate. Do NOT invent revenue, headcount,
  decision-makers, geography, industry, or company facts that are not provided.
- Unknown fields must stay unknown — put them in uncertainties.
- Do NOT invent candidate_ids. Every selected row must use an input candidate_id.
- Do NOT invent companies that are not in the input list.
- selection_score is 0–100 pre-research priority only (NOT a final Fit score).
- Prefer companies that look like the ICP's priority business types and themes.
- Prefer verified domains, clear company identity, useful location, and buyer titles
  when evidence exists.
- Penalize weak/uncertain identity and thin metadata — but do NOT discard a strong
  ICP match solely because another candidate has richer metadata.
- Do NOT apply source quotas. All surviving candidates compete equally.
- Do NOT prefer a candidate merely because a provider returned richer metadata.
- Hard exclusions are already applied in code — do not re-admit excluded firms.
- Respect geography and revenue targets as soft preference when evidence exists;
  never fabricate a revenue number.
- This step is comparison/reasoning only — do NOT pretend to browse the web.
- Distinguish evidence (facts from the input) from uncertainties (missing facts).
"""


@dataclass(slots=True)
class TriageItem:
    candidate_id: str
    candidate: DiscoveryCandidate
    triage_score: float
    rank: int
    reason: str
    positive_signals: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    possible_exclusion: str | None = None
    selected: bool = False
    selection_summary: str | None = None


@dataclass(slots=True)
class TriageResult:
    items: list[TriageItem]
    selected: list[DiscoveryCandidate]
    model: str | None
    source: str  # ai | deterministic_fallback
    ok: bool
    error: str | None = None
    requested: int = 0
    pool_size: int = 0
    selection_summary: str | None = None
    considered_count: int = 0


def _icp_payload(icp: dict[str, Any]) -> dict[str, Any]:
    """Compact but complete Admin ICP targeting config for triage."""
    geo = icp.get("geography") or {}
    revenue = icp.get("revenue") or {}
    return {
        "geography": {
            "nationwide": geo.get("nationwide"),
            "countries": geo.get("countries"),
            "states": geo.get("states"),
        },
        "revenue": {
            "primary_min_millions": revenue.get("primary_min_millions"),
            "primary_max_millions": revenue.get("primary_max_millions"),
            "sweet_spot_min_millions": revenue.get("sweet_spot_min_millions"),
            "sweet_spot_max_millions": revenue.get("sweet_spot_max_millions"),
        },
        "buyer_titles": icp.get("buyer_titles") or [],
        "fit_themes": icp.get("fit_themes") or [],
        "priority_categories": icp.get("priority_categories") or [],
        "excluded_categories": icp.get("excluded_categories") or [],
        "notes": icp.get("notes") or None,
        "score_weights": icp.get("score_weights") or None,
    }


def _desc_limit(pool_size: int) -> int:
    return _DESC_MAX_COMPACT if pool_size > 40 else _DESC_MAX_DEFAULT


def candidate_payload(
    candidate_id: str,
    cand: DiscoveryCandidate,
    *,
    desc_limit: int = _DESC_MAX_DEFAULT,
) -> dict[str, Any]:
    verified = bool((cand.raw or {}).get("website_verified"))
    desc = (cand.description or cand.notes or "").strip()
    if desc:
        desc = desc[:desc_limit]
    else:
        desc = None
    category = None
    if cand.slice_key and "|" in str(cand.slice_key):
        parts = str(cand.slice_key).split("|")
        if len(parts) > 1 and parts[1].strip():
            category = parts[1].strip()
    if not category:
        category = cand.industry
    return {
        "candidate_id": candidate_id,
        "company_name": cand.company,
        "company": cand.company,  # alias for older parsers / tests
        "domain": cand.domain,
        "website": cand.website,
        "source": cand.discovery_source,
        "discovery_source": cand.discovery_source,
        "category": category,
        "discovery_slice": cand.slice_key,
        "slice_key": cand.slice_key,
        "city": cand.city,
        "state": cand.state,
        "description": desc,
        "employee_headcount_hint": cand.headcount_hint,
        "headcount_hint": cand.headcount_hint,
        "revenue_hint_usd": cand.revenue_hint_usd,
        "executive_name": cand.executive_name,
        "executive_title": cand.executive_title,
        "website_verification_status": "verified" if verified else ("unverified" if cand.domain else "no_domain"),
        "website_verified": verified,
        "verify_title": (cand.raw or {}).get("verify_title"),
        "identity_uncertain": bool(cand.identity_uncertain),
        "npi": cand.npi,
    }


def serialize_candidate_for_pool(cand: DiscoveryCandidate) -> dict[str, Any]:
    """Persist a surviving candidate for later AI-selection replay."""
    raw = cand.raw or {}
    return {
        "company": cand.company,
        "domain": cand.domain,
        "website": cand.website,
        "city": cand.city,
        "state": cand.state,
        "region": cand.region,
        "industry": cand.industry,
        "description": (cand.description or "")[:800] or None,
        "notes": (cand.notes or "")[:800] or None,
        "discovery_source": cand.discovery_source,
        "slice_key": cand.slice_key,
        "executive_name": cand.executive_name,
        "executive_title": cand.executive_title,
        "headcount_hint": cand.headcount_hint,
        "revenue_hint_usd": cand.revenue_hint_usd,
        "npi": cand.npi,
        "identity_uncertain": bool(cand.identity_uncertain),
        "source_url": cand.source_url,
        "source_type": cand.source_type,
        "postal_code": cand.postal_code,
        "company_phone": cand.company_phone,
        "raw": {
            k: raw.get(k)
            for k in ("website_verified", "verify_title", "npi", "taxonomy", "locations")
            if k in raw
        },
    }


def deserialize_candidate_from_pool(row: dict[str, Any]) -> DiscoveryCandidate:
    raw = dict(row.get("raw") or {})
    return DiscoveryCandidate(
        company=str(row.get("company") or "").strip() or "Unknown",
        website=row.get("website"),
        domain=row.get("domain"),
        city=row.get("city"),
        state=row.get("state"),
        region=row.get("region"),
        industry=row.get("industry"),
        source="autonomous_discovery",
        source_url=row.get("source_url"),
        source_type=str(row.get("source_type") or "search"),
        notes=row.get("notes"),
        identity_uncertain=bool(row.get("identity_uncertain")),
        raw=raw,
        discovery_source=row.get("discovery_source") or row.get("source"),
        slice_key=row.get("slice_key"),
        npi=row.get("npi"),
        description=row.get("description"),
        executive_name=row.get("executive_name"),
        executive_title=row.get("executive_title"),
        company_phone=row.get("company_phone"),
        headcount_hint=row.get("headcount_hint"),
        revenue_hint_usd=row.get("revenue_hint_usd"),
        postal_code=row.get("postal_code"),
    )


def deterministic_triage_score(cand: DiscoveryCandidate, icp: dict[str, Any]) -> tuple[float, list[str]]:
    """Source-neutral evidence score used when AI triage is unavailable."""
    score = 10.0
    signals: list[str] = []
    priority = [str(x).lower() for x in (icp.get("priority_categories") or []) if str(x).strip()]
    industry = (cand.industry or "").lower()
    company = (cand.company or "").lower()
    desc = (cand.description or cand.notes or "").lower()

    if cand.domain:
        score += 12
        signals.append("has company domain")
    if (cand.raw or {}).get("website_verified"):
        score += 18
        signals.append("website verified")
    if any(p and (p in industry or p in company or p in desc) for p in priority):
        score += 22
        signals.append("priority category match")
    if cand.executive_name and "unknown" not in (cand.executive_name or "").lower():
        score += 14
        signals.append("decision-maker present")
    if cand.executive_title:
        score += 6
        signals.append("title present")
    if cand.description or cand.notes:
        score += 10
        signals.append("company description present")
    if cand.city or cand.state:
        score += 8
        signals.append("location present")
    if cand.headcount_hint:
        score += 8
        signals.append("headcount hint present")
    if cand.identity_uncertain:
        score -= 12
        signals.append("identity uncertain")
    return max(0.0, min(100.0, score)), signals


def candidate_pool_target(requested: int, *, settings: Settings) -> int:
    """Legacy helper retained for tests/compat — NOT used to truncate AI input.

    Global triage now sends the full surviving pool (bounded only by
    discovery_global_triage_max_candidates).
    """
    mult = max(1, int(getattr(settings, "discovery_triage_pool_multiplier", 3) or 3))
    hard = max(int(requested), int(getattr(settings, "discovery_triage_pool_hard_max", 60) or 60))
    scan = max(int(requested), int(getattr(settings, "discovery_scan_hard_max", 400) or 400))
    return max(int(requested), min(int(requested) * mult, hard, scan))


def global_triage_max_candidates(settings: Settings) -> int:
    return max(1, int(getattr(settings, "discovery_global_triage_max_candidates", 250) or 250))


class DiscoveryCandidateTriageService:
    def __init__(
        self,
        db: Session | None = None,
        *,
        settings: Settings | None = None,
        openai_call: Callable[[list[dict[str, str]], str], str] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._openai_call = openai_call

    def triage(
        self,
        *,
        icp: dict[str, Any],
        candidates: list[DiscoveryCandidate],
        select_count: int,
    ) -> TriageResult:
        select_count = max(0, int(select_count))
        if not candidates:
            return TriageResult(
                items=[],
                selected=[],
                model=None,
                source="deterministic_fallback",
                ok=True,
                requested=select_count,
                pool_size=0,
                considered_count=0,
            )

        indexed: list[tuple[str, DiscoveryCandidate]] = []
        for i, cand in enumerate(candidates, start=1):
            cid = f"cand_{i}"
            cand.raw = dict(cand.raw or {})
            cand.raw["candidate_id"] = cid
            indexed.append((cid, cand))

        model = (
            (getattr(self.settings, "research_validation_model", None) or "").strip()
            or (self.settings.openai_model or "").strip()
            or "gpt-4o-mini"
        )
        try:
            messages = self._messages(icp, indexed, select_count=select_count)
            raw = (self._openai_call or self._default_openai_call)(messages, model)
            parsed = self._parse_json(raw)
            items = self._normalize_ai_selection(parsed, indexed, select_count=select_count)
            selection_summary = None
            if isinstance(parsed, dict):
                selection_summary = str(parsed.get("selection_summary") or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            logger.info("discovery candidate triage unavailable: %s", exc.__class__.__name__)
            return self._fallback(
                indexed,
                icp=icp,
                select_count=select_count,
                model=model,
                error=exc.__class__.__name__,
            )

        for i, it in enumerate(items, start=1):
            it.rank = i
            it.selected = True
            it.selection_summary = selection_summary
            it.candidate.raw["triage"] = {
                "score": it.triage_score,
                "rank": it.rank,
                "reason": it.reason,
                "positive_signals": it.positive_signals,
                "uncertainties": it.uncertainties,
                "possible_exclusion": it.possible_exclusion,
                "selected": True,
                "source": "ai",
                "selection_summary": selection_summary,
            }
        selected = [it.candidate for it in items]
        return TriageResult(
            items=items,
            selected=selected,
            model=model,
            source="ai",
            ok=True,
            requested=select_count,
            pool_size=len(candidates),
            considered_count=len(candidates),
            selection_summary=selection_summary,
        )

    def _fallback(
        self,
        indexed: list[tuple[str, DiscoveryCandidate]],
        *,
        icp: dict[str, Any],
        select_count: int,
        model: str | None,
        error: str,
    ) -> TriageResult:
        scored: list[TriageItem] = []
        for cid, cand in indexed:
            score, signals = deterministic_triage_score(cand, icp)
            scored.append(
                TriageItem(
                    candidate_id=cid,
                    candidate=cand,
                    triage_score=score,
                    rank=0,
                    reason="Deterministic pre-research ranking (AI triage unavailable).",
                    positive_signals=signals,
                    uncertainties=["AI triage unavailable — used evidence completeness only"],
                )
            )
        scored.sort(
            key=lambda it: (
                -it.triage_score,
                it.candidate.discovery_source or "",
                (it.candidate.company or "").lower(),
            )
        )
        diversified = self._diversify(scored)
        selected_items: list[TriageItem] = []
        for i, it in enumerate(diversified, start=1):
            it.rank = i
            it.selected = i <= select_count
            it.candidate.raw["triage"] = {
                "score": it.triage_score,
                "rank": it.rank,
                "reason": it.reason,
                "positive_signals": it.positive_signals,
                "uncertainties": it.uncertainties,
                "possible_exclusion": None,
                "selected": it.selected,
                "source": "deterministic_fallback",
            }
            if it.selected:
                selected_items.append(it)
        return TriageResult(
            items=selected_items,
            selected=[it.candidate for it in selected_items],
            model=model,
            source="deterministic_fallback",
            ok=False,
            error=error,
            requested=select_count,
            pool_size=len(indexed),
            considered_count=len(indexed),
            selection_summary="Deterministic fallback — AI triage unavailable.",
        )

    @staticmethod
    def _diversify(items: list[TriageItem]) -> list[TriageItem]:
        """Preserve score order while lightly rotating sources among close scores."""
        if len(items) <= 2:
            return items
        out: list[TriageItem] = []
        remaining = list(items)
        last_source: str | None = None
        while remaining:
            pick_idx = 0
            top = remaining[0].triage_score
            for i, it in enumerate(remaining):
                if top - it.triage_score > 8:
                    break
                src = it.candidate.discovery_source or ""
                if last_source and src != last_source:
                    pick_idx = i
                    break
            chosen = remaining.pop(pick_idx)
            out.append(chosen)
            last_source = chosen.candidate.discovery_source or ""
        return out

    def _messages(
        self,
        icp: dict[str, Any],
        indexed: list[tuple[str, DiscoveryCandidate]],
        *,
        select_count: int,
    ) -> list[dict[str, str]]:
        desc_limit = _desc_limit(len(indexed))
        candidates_payload = [
            candidate_payload(cid, cand, desc_limit=desc_limit) for cid, cand in indexed
        ]
        # Shrink descriptions further if payload is still large.
        blob = json.dumps(candidates_payload, default=str)
        if len(blob) > _PAYLOAD_SOFT_CHAR_BUDGET and desc_limit > 80:
            tighter = max(80, desc_limit // 2)
            candidates_payload = [
                candidate_payload(cid, cand, desc_limit=tighter) for cid, cand in indexed
            ]
            blob = json.dumps(candidates_payload, default=str)
        logger.info(
            "discovery global triage payload candidates=%s select=%s approx_chars=%s",
            len(indexed),
            select_count,
            len(blob) + 2000,
        )
        payload = {
            "prompt_version": PROMPT_VERSION,
            "requested_count": select_count,
            "considered_count": len(indexed),
            "icp": _icp_payload(icp),
            "candidates": candidates_payload,
            "required_json_schema": {
                "considered_count": len(indexed),
                "requested_count": select_count,
                "selected": [
                    {
                        "candidate_id": "cand_N from input",
                        "rank": 1,
                        "selection_score": "0-100 number",
                        "reason": "short string based on supplied evidence",
                        "evidence": ["string"],
                        "uncertainties": ["string"],
                    }
                ],
                "selection_summary": "short string",
            },
        }
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Select the best {select_count} candidates from the full pool of "
                    f"{len(indexed)} for pre-research priority against the ICP. "
                    "Return ONLY JSON with considered_count, requested_count, selected "
                    "(exactly the requested count when enough candidates exist), and "
                    "selection_summary. Do not invent candidate_ids.\n\n"
                    f"{json.dumps(payload, default=str)}"
                ),
            },
        ]

    def _default_openai_call(self, messages: list[dict[str, str]], model: str) -> str:
        openai = OpenAIService(db=self.db, settings=self.settings)
        api_key, configured_model = openai.resolve_credentials()
        if not api_key:
            api_key = (self.settings.openai_api_key or "").strip() or None
        if not api_key:
            raise RuntimeError("openai_not_configured")
        chosen = (model or configured_model or "gpt-4o-mini").strip() or "gpt-4o-mini"
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=chosen,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            match = _JSON_OBJECT.search(text)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None

    def _normalize_ai_selection(
        self,
        parsed: dict[str, Any] | None,
        indexed: list[tuple[str, DiscoveryCandidate]],
        *,
        select_count: int,
    ) -> list[TriageItem]:
        if not parsed:
            raise ValueError("empty_ai_payload")
        by_id = {cid: cand for cid, cand in indexed}
        rows = parsed.get("selected")
        # Legacy v1 schema: full candidates[] ranking covering every id.
        if not isinstance(rows, list):
            legacy = parsed.get("candidates")
            if isinstance(legacy, list):
                return self._normalize_legacy_candidates(legacy, by_id, select_count=select_count)
            raise ValueError("missing_selected_array")

        want = min(select_count, len(indexed))
        seen: set[str] = set()
        items: list[TriageItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("candidate_id") or "").strip()
            if cid not in by_id or cid in seen:
                continue
            seen.add(cid)
            try:
                score = float(row.get("selection_score", row.get("triage_score")))
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(100.0, score))
            reason = str(row.get("reason") or "No reason provided").strip()[:400]
            evidence = [str(x) for x in (row.get("evidence") or row.get("positive_signals") or []) if str(x).strip()][
                :8
            ]
            uncertainties = [str(x) for x in (row.get("uncertainties") or []) if str(x).strip()][:8]
            items.append(
                TriageItem(
                    candidate_id=cid,
                    candidate=by_id[cid],
                    triage_score=score,
                    rank=0,
                    reason=reason,
                    positive_signals=evidence,
                    uncertainties=uncertainties,
                )
            )
            if len(items) >= want:
                break

        if len(items) != want:
            raise ValueError("selected_count_mismatch")
        # Preserve model-provided order when ranks present; else score order.
        items.sort(
            key=lambda it: (
                next(
                    (
                        int(r.get("rank"))
                        for r in rows
                        if isinstance(r, dict) and str(r.get("candidate_id") or "").strip() == it.candidate_id
                        and str(r.get("rank") or "").isdigit()
                    ),
                    10_000,
                ),
                -it.triage_score,
                it.candidate_id,
            )
        )
        return items

    def _normalize_legacy_candidates(
        self,
        rows: list[Any],
        by_id: dict[str, DiscoveryCandidate],
        *,
        select_count: int,
    ) -> list[TriageItem]:
        seen: set[str] = set()
        items: list[TriageItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("candidate_id") or "").strip()
            if cid not in by_id or cid in seen:
                continue
            seen.add(cid)
            try:
                score = float(row.get("triage_score", row.get("selection_score")))
            except (TypeError, ValueError):
                score = 0.0
            score = max(0.0, min(100.0, score))
            reason = str(row.get("reason") or "No reason provided").strip()[:400]
            positives = [str(x) for x in (row.get("positive_signals") or row.get("evidence") or []) if str(x).strip()][
                :8
            ]
            uncertainties = [str(x) for x in (row.get("uncertainties") or []) if str(x).strip()][:8]
            excl = row.get("possible_exclusion")
            excl_s = str(excl).strip()[:200] if excl else None
            items.append(
                TriageItem(
                    candidate_id=cid,
                    candidate=by_id[cid],
                    triage_score=score,
                    rank=0,
                    reason=reason,
                    positive_signals=positives,
                    uncertainties=uncertainties,
                    possible_exclusion=excl_s or None,
                )
            )
        if len(items) != len(by_id):
            raise ValueError("candidate_id_mismatch")
        items.sort(key=lambda it: (-it.triage_score, it.candidate_id))
        want = min(select_count, len(items))
        return items[:want]
