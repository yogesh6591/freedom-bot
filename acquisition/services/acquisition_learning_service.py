"""Acquisition learning loop — deterministic outcomes + AI-assisted prioritization.

Effective ranking:
  effective_priority = clamp(base_priority + ai_adjustment, 0, 100)

Deterministic backend computes factual historical metrics and comparable sets.
A configurable Admin OpenAI model interprets those facts for one prospect.
Python clamps and persists the result. Base Fit/Timing/Reachability/Priority
scores are never mutated by learning. Opens/clicks are never optimization signals.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.config import Settings, get_settings
from acquisition.models.assessment import AssessmentProfile
from acquisition.models.campaign import (
    AssessmentStatus,
    CampaignProspect,
    ConsultationShowStatus,
    PurchaseOutcome,
)
from acquisition.models.journey import QualificationStatus
from acquisition.models.prospect import AcquisitionStatus, Prospect
from acquisition.services.ai_outcome_learning_service import (
    ALLOWED_MODELS,
    AiOutcomeLearningResult,
    AiOutcomeLearningService,
    PROMPT_VERSION,
)
from acquisition.services.booking_service import BOOKING_STATUS_CONFIRMED
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services import acquisition_trace as acq_trace

_SIGNAL_KEYWORDS = (
    ("hiring", "hiring"),
    ("growth", "growth"),
    ("expansion", "expansion"),
    ("funding", "funding"),
    ("leadership", "leadership"),
    ("clinic", "expansion"),
    ("roles", "hiring"),
)

_TITLE_BUCKETS = (
    ("founder", ("founder", "co-founder", "cofounder", "owner", "principal")),
    ("ceo", ("ceo", "chief executive")),
    ("president", ("president",)),
    ("partner", ("partner", "managing partner")),
    ("coo", ("coo", "chief operating")),
    ("cfo", ("cfo", "chief financial")),
    ("vp", ("vp ", "v.p", "vice president")),
    ("director", ("director",)),
)


def _money(value: Any) -> float:
    if value is None:
        return 0.0
    return float(Decimal(str(value)))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _score_band(score: float | None) -> str:
    if score is None:
        return "unknown"
    s = float(score)
    if s < 20:
        return "0-19"
    if s < 40:
        return "20-39"
    if s < 60:
        return "40-59"
    if s < 80:
        return "60-79"
    return "80-100"


def _norm_key(value: str | None, *, default: str = "unknown") -> str:
    text = (value or "").strip()
    return text.lower() if text else default


def _message_angle(key: str | None) -> str | None:
    raw = (key or "").strip()
    if not raw:
        return None
    if raw.startswith("autonomous:"):
        return raw.split(":", 1)[1].strip() or None
    return raw


def _signal_categories(prospect: Prospect, link: CampaignProspect | None) -> list[str]:
    cats: set[str] = set()
    for src in prospect.research_sources or []:
        cat = (src.category or "").strip().lower()
        if cat and cat not in {"discovery", "other"}:
            cats.add(cat)
    blob_parts: list[str] = []
    if isinstance(prospect.public_signals, list):
        blob_parts.extend(str(x) for x in prospect.public_signals)
    elif isinstance(prospect.public_signals, dict):
        blob_parts.extend(f"{k} {v}" for k, v in prospect.public_signals.items())
    if link and link.outreach_signal_snapshot:
        blob_parts.append(link.outreach_signal_snapshot)
    blob = " ".join(blob_parts).lower()
    for needle, label in _SIGNAL_KEYWORDS:
        if needle in blob:
            cats.add(label)
    return sorted(cats) or (["unattributed"] if blob else [])


def _title_bucket(title: str | None) -> str:
    text = (title or "").strip().lower()
    if not text:
        return "unknown"
    for bucket, needles in _TITLE_BUCKETS:
        if any(n in text for n in needles):
            return bucket
    return text[:48]


def _empty_bucket() -> dict[str, Any]:
    return {
        "sample": 0,
        "contacted": 0,
        "assessment_started": 0,
        "assessment_completed": 0,
        "qualified": 0,
        "booked": 0,
        "showed": 0,
        "purchased": 0,
        "revenue": 0.0,
        "high_fit_no_contact": 0,
    }


class AcquisitionLearningService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        ai_learning: AiOutcomeLearningService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.icp = IcpSettingsService(db)
        self._ai_override = ai_learning
        self.ai = ai_learning or self._build_ai_service()

    def _resolve_learning_model(self) -> str:
        cfg = self.icp.as_dict()
        chosen = (
            str(cfg.get("learning_model") or "").strip()
            or (self.settings.outcome_learning_model or "").strip()
            or (self.settings.openai_model or "").strip()
            or "gpt-4o-mini"
        )
        return chosen if chosen in ALLOWED_MODELS else "gpt-4o-mini"

    def _build_ai_service(self) -> AiOutcomeLearningService:
        return AiOutcomeLearningService(
            self.db,
            settings=self.settings,
            model_override=self._resolve_learning_model(),
        )

    def _ai_for_request(self) -> AiOutcomeLearningService:
        if self._ai_override is not None:
            # Tests/injected clients keep their mock; still prefer Admin model when set.
            model = self._resolve_learning_model()
            if getattr(self._ai_override, "_model_override", None) is None:
                self._ai_override._model_override = model  # type: ignore[attr-defined]
            return self._ai_override
        return self._build_ai_service()

    def recompute_learning_metrics(self) -> dict[str, Any]:
        """Rebuild materialized learning summary and bump profile version."""
        metrics = self._compute_metrics()
        row = self.icp.get_or_create()
        row.learning_metrics_json = metrics
        row.learning_recomputed_at = datetime.now(timezone.utc)
        row.learning_profile_version = int(row.learning_profile_version or 0) + 1
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return metrics

    def get_learning_summary(self, *, recompute_if_missing: bool = True) -> dict[str, Any]:
        cfg = self.icp.as_dict()
        cached = cfg.get("learning_metrics_json")
        if not cached and recompute_if_missing:
            cached = self.recompute_learning_metrics()
            cfg = self.icp.as_dict()
        summary = dict(cached or {})
        min_n = int(cfg.get("learning_min_sample_size") or 25)
        contacted = int((summary.get("totals") or {}).get("contacted") or 0)
        ai = self._ai_for_request()
        openai_ok = ai.openai_available() or ai.can_invoke()
        summary["learning_enabled"] = bool(cfg.get("learning_enabled"))
        summary["learning_min_sample_size"] = min_n
        summary["learning_max_adjustment"] = float(cfg.get("learning_max_adjustment") or 8)
        summary["learning_recomputed_at"] = cfg.get("learning_recomputed_at")
        summary["learning_profile_version"] = int(cfg.get("learning_profile_version") or 0)
        summary["learning_model"] = self._resolve_learning_model()
        summary["contacted_count"] = contacted
        summary["sample_ready"] = contacted >= min_n
        summary["openai_available"] = openai_ok
        summary["outcome_learning_model"] = summary["learning_model"]
        if not openai_ok:
            summary["status_code"] = "openai_unavailable"
            summary["status_message"] = (
                "AI learning unavailable — base priority scoring continues normally."
            )
        elif not bool(cfg.get("learning_enabled")):
            if contacted >= min_n:
                summary["status_code"] = "ready_disabled"
                summary["status_message"] = (
                    "Enough outcome history is available. AI learning is currently disabled."
                )
            else:
                summary["status_code"] = "collecting"
                summary["status_message"] = (
                    f"Collecting outcome data — {contacted} of {min_n} contacted "
                    "prospects available."
                )
        elif contacted < min_n:
            summary["status_code"] = "collecting"
            summary["status_message"] = (
                f"Collecting outcome data — {contacted} of {min_n} contacted "
                "prospects available."
            )
        else:
            summary["status_code"] = "active"
            summary["status_message"] = "AI-assisted outcome prioritization is active."
        return summary

    def get_learning_adjustment(
        self, prospect: Prospect, *, force_refresh: bool = False
    ) -> dict[str, Any]:
        """Bounded AI adjustment for one prospect. Never mutates base Priority Score."""
        cfg = self.icp.as_dict()
        base = float(prospect.priority_score or 0.0)
        profile_version = int(cfg.get("learning_profile_version") or 0)
        model_name = self._resolve_learning_model()
        if not cfg.get("learning_enabled"):
            result = self._neutral_result(
                base=base,
                enabled=False,
                reasons=["AI outcome learning disabled."],
                insufficient_data=False,
                profile_version=profile_version,
                openai_called=False,
                model=model_name,
            )
            acq_trace.trace_ai_learning_skipped(
                enabled=False,
                model=model_name,
                historical_sample=0,
                minimum_required=int(cfg.get("learning_min_sample_size") or 25),
                reason="AI learning skipped: learning disabled",
            )
            return result

        summary = self.get_learning_summary(recompute_if_missing=True)
        min_n = int(cfg.get("learning_min_sample_size") or 25)
        max_adj = float(cfg.get("learning_max_adjustment") or 8)
        contacted = int((summary.get("totals") or {}).get("contacted") or 0)
        if contacted < min_n:
            reason = (
                f"AI learning skipped: historical sample {contacted}/{min_n}"
            )
            acq_trace.trace_ai_learning_skipped(
                enabled=True,
                model=model_name,
                historical_sample=contacted,
                minimum_required=min_n,
                reason=reason,
            )
            return self._neutral_result(
                base=base,
                enabled=True,
                reasons=[
                    f"Collecting outcome data — {contacted} of {min_n} contacted "
                    "prospects available."
                ],
                insufficient_data=True,
                profile_version=profile_version,
                openai_called=False,
                model=model_name,
                historical_sample_size=contacted,
            )

        if not force_refresh and self._cached_adjustment_is_fresh(prospect, profile_version):
            return {
                "enabled": True,
                "base_priority_score": base,
                "learning_adjustment": float(prospect.learning_adjustment or 0.0),
                "effective_priority_score": float(
                    prospect.effective_priority_score
                    if prospect.effective_priority_score is not None
                    else base
                ),
                "reasons": list(prospect.learning_adjustment_reasons or []),
                "insufficient_data": False,
                "confidence": prospect.learning_confidence,
                "model": prospect.learning_model or model_name,
                "learning_profile_version": prospect.learning_profile_version,
                "prompt_version": prospect.learning_prompt_version,
                "historical_sample_size": prospect.learning_historical_sample_size,
                "matched_patterns": list(prospect.learning_matched_patterns or [])
                if isinstance(prospect.learning_matched_patterns, list)
                else [],
                "openai_called": False,
                "cached": True,
            }

        ai = self._ai_for_request()
        if not ai.can_invoke():
            reason = "AI learning skipped: OpenAI unavailable"
            acq_trace.trace_ai_learning_skipped(
                enabled=True,
                model=model_name,
                historical_sample=contacted,
                minimum_required=min_n,
                reason=reason,
            )
            return self._neutral_result(
                base=base,
                enabled=True,
                reasons=[
                    "AI learning unavailable — base priority scoring continues normally."
                ],
                insufficient_data=True,
                profile_version=profile_version,
                openai_called=False,
                error="openai_unavailable",
                model=model_name,
            )

        prospect_payload = self._prospect_payload(prospect)
        comparables = self._find_comparable_contacted_prospects(
            prospect, limit=max(min_n, 40)
        )
        comparable_n = len(comparables)
        relevant = self._relevant_cohorts(prospect, summary, min_n=min(5, max(3, min_n // 5)))
        outcome_summary = self._summarize_comparable_outcomes(comparables)
        ai_input_summary = {
            "totals": {
                "contacted": (summary.get("totals") or {}).get("contacted"),
                "qualified": (summary.get("totals") or {}).get("qualified"),
                "booked": (summary.get("totals") or {}).get("booked"),
                "purchased": (summary.get("totals") or {}).get("purchased"),
                "revenue": (summary.get("totals") or {}).get("revenue"),
            },
            "baselines": summary.get("baselines") or {},
            "relevant_cohorts": relevant,
            "comparable_prospects": comparables,
            "comparable_outcome_summary": outcome_summary,
            "historical_sample_size": comparable_n,
        }
        ai_result = ai.recommend_adjustment(
            prospect_payload=prospect_payload,
            historical_summary=ai_input_summary,
            maximum_adjustment=max_adj,
        )
        result = self._result_from_ai(
            base=base,
            ai_result=ai_result,
            max_adj=max_adj,
            profile_version=profile_version,
            historical_sample_size=comparable_n,
        )
        if acq_trace.is_acquisition_trace_enabled():
            dims = {
                "industry": prospect.industry,
                "revenue_band": prospect.revenue_band,
                "headcount_band": prospect.headcount_band,
                "state": prospect.state,
                "region": prospect.region,
                "buyer_title": prospect.executive_title,
                "fit_band": _score_band(prospect.fit_score),
                "timing_band": _score_band(prospect.timing_score),
                "reachability_band": _score_band(prospect.reachability_score),
                "source": prospect.source,
                "fit_themes": list(prospect.fit_reasons or [])[:4],
                "timing_signals": list(prospect.timing_reasons or [])[:4],
            }
            acq_trace.trace_ai_learning_applied(
                enabled=True,
                model=result.get("model") or model_name,
                historical_sample=contacted,
                minimum_required=min_n,
                comparable_count=comparable_n,
                dimensions=dims,
                outcomes_summary={
                    "contacted": comparable_n,
                    "qualified": outcome_summary.get("qualified"),
                    "booked": outcome_summary.get("booked"),
                    "purchased": outcome_summary.get("purchased"),
                    "revenue": outcome_summary.get("revenue"),
                },
                adjustment=float(result["learning_adjustment"]),
                confidence=result.get("confidence"),
                reason=(result.get("reasons") or ["unknown"])[0],
                matched_patterns=list(result.get("matched_patterns") or []),
                base_priority=base,
                effective_priority=float(result["effective_priority_score"]),
            )
        return result

    def apply_learning_to_prospect(
        self,
        prospect: Prospect,
        *,
        persist: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        result = self.get_learning_adjustment(prospect, force_refresh=force_refresh)
        prospect.learning_adjustment = float(result["learning_adjustment"])
        prospect.learning_adjustment_reasons = list(result["reasons"])
        prospect.effective_priority_score = float(result["effective_priority_score"])
        prospect.learning_confidence = (
            float(result["confidence"]) if result.get("confidence") is not None else None
        )
        prospect.learning_model = result.get("model")
        prospect.learning_profile_version = result.get("learning_profile_version")
        prospect.learning_prompt_version = result.get("prompt_version") or PROMPT_VERSION
        if result.get("historical_sample_size") is not None:
            prospect.learning_historical_sample_size = int(result["historical_sample_size"])
        patterns = result.get("matched_patterns") or result.get("supporting_patterns") or []
        prospect.learning_matched_patterns = list(patterns) if patterns else None
        if result.get("openai_called") or result.get("cached") or force_refresh:
            prospect.learning_evaluated_at = datetime.now(timezone.utc)
        elif prospect.learning_evaluated_at is None and not result.get("insufficient_data"):
            prospect.learning_evaluated_at = datetime.now(timezone.utc)
        # Neutral collecting/disabled states still stamp evaluated_at so pages don't re-call AI.
        if prospect.learning_evaluated_at is None:
            prospect.learning_evaluated_at = datetime.now(timezone.utc)
        if persist:
            self.db.add(prospect)
            self.db.commit()
            self.db.refresh(prospect)
        return result

    def refresh_all_prospect_adjustments(
        self, *, limit: int | None = None, force_refresh: bool = True
    ) -> dict[str, Any]:
        """Recompute metrics, bump profile version, refresh a bounded prospect batch."""
        self.recompute_learning_metrics()
        hard = max(1, int(self.settings.outcome_learning_refresh_batch_max or 25))
        batch = hard if limit is None else max(1, min(int(limit), hard * 4))
        rows = list(
            self.db.scalars(
                select(Prospect)
                .where(Prospect.priority_score.is_not(None))
                .order_by(Prospect.updated_at.desc())
                .limit(batch)
            ).all()
        )
        updated = 0
        openai_calls = 0
        for prospect in rows:
            result = self.apply_learning_to_prospect(
                prospect, persist=False, force_refresh=force_refresh
            )
            updated += 1
            if result.get("openai_called"):
                openai_calls += 1
        self.db.commit()
        return {
            "updated": updated,
            "openai_calls": openai_calls,
            "batch_limit": batch,
            "learning_profile_version": int(
                self.icp.as_dict().get("learning_profile_version") or 0
            ),
        }

    def high_fit_unreachable_summary(self) -> dict[str, Any]:
        summary = self.get_learning_summary(recompute_if_missing=True)
        return summary.get("high_fit_no_contact") or {
            "count": 0,
            "by_industry": [],
            "note": "High-fit prospects lacking a legitimate contact path.",
        }

    def has_valid_ai_adjustment(self, prospect: Prospect) -> bool:
        """True when queue should prefer effective_priority_score."""
        cfg = self.icp.as_dict()
        if not cfg.get("learning_enabled"):
            return False
        if prospect.effective_priority_score is None:
            return False
        if prospect.learning_evaluated_at is None:
            return False
        current = int(cfg.get("learning_profile_version") or 0)
        stored = prospect.learning_profile_version
        if stored is not None and int(stored) != current:
            return False
        return True

    # ----- helpers -----

    def _cached_adjustment_is_fresh(self, prospect: Prospect, profile_version: int) -> bool:
        if prospect.learning_evaluated_at is None:
            return False
        if prospect.learning_profile_version is None:
            return False
        if int(prospect.learning_profile_version) != int(profile_version):
            return False
        if prospect.scored_at and prospect.learning_evaluated_at < prospect.scored_at:
            return False
        return True

    def _prospect_payload(self, prospect: Prospect) -> dict[str, Any]:
        signals: list[str] = []
        if isinstance(prospect.public_signals, list):
            signals = [str(x)[:200] for x in prospect.public_signals[:8]]
        elif isinstance(prospect.public_signals, dict):
            signals = [f"{k}: {v}"[:200] for k, v in list(prospect.public_signals.items())[:8]]
        return {
            "industry": prospect.industry,
            "business_type": prospect.industry,
            "revenue_band": prospect.revenue_band,
            "headcount_band": prospect.headcount_band,
            "state": prospect.state,
            "region": prospect.region,
            "source": prospect.source,
            "buyer_title": prospect.executive_title,
            "buyer_title_bucket": _title_bucket(prospect.executive_title),
            "is_founder_or_equivalent": bool(prospect.is_founder_or_equivalent),
            "fit_score": prospect.fit_score,
            "fit_band": _score_band(prospect.fit_score),
            "timing_score": prospect.timing_score,
            "timing_band": _score_band(prospect.timing_score),
            "reachability_score": prospect.reachability_score,
            "reachability_band": _score_band(prospect.reachability_score),
            "base_priority_score": prospect.priority_score,
            "priority_band": _score_band(prospect.priority_score),
            "signals": signals,
            "signal_categories": _signal_categories(prospect, None),
            "fit_themes": list(prospect.fit_reasons or [])[:5],
            "timing_signals": list(prospect.timing_reasons or [])[:5],
        }

    def _find_comparable_contacted_prospects(
        self, prospect: Prospect, *, limit: int = 40
    ) -> list[dict[str, Any]]:
        """Rank previously contacted prospects by structured similarity (not same company)."""
        links = list(
            self.db.scalars(
                select(CampaignProspect).options(
                    selectinload(CampaignProspect.prospect).selectinload(
                        Prospect.research_sources
                    ),
                )
            ).all()
        )
        profiles = {
            p.campaign_prospect_id: p
            for p in self.db.scalars(select(AssessmentProfile)).all()
        }
        target = {
            "industry": _norm_key(prospect.industry),
            "revenue_band": _norm_key(prospect.revenue_band),
            "headcount_band": _norm_key(prospect.headcount_band),
            "state": _norm_key(prospect.state),
            "region": _norm_key(prospect.region),
            "source": _norm_key(prospect.source),
            "title": _title_bucket(prospect.executive_title),
            "fit_band": _score_band(prospect.fit_score),
            "timing_band": _score_band(prospect.timing_score),
            "reach_band": _score_band(prospect.reachability_score),
            "priority_band": _score_band(prospect.priority_score),
            "signals": set(_signal_categories(prospect, None)),
            "fit_themes": {
                _norm_key(str(x), default="") for x in (prospect.fit_reasons or []) if x
            }
            - {""},
            "timing_themes": {
                _norm_key(str(x), default="") for x in (prospect.timing_reasons or []) if x
            }
            - {""},
        }
        best_by_prospect: dict[Any, dict[str, Any]] = {}
        for link in links:
            other = link.prospect
            if other is None or other.id == prospect.id:
                continue
            # Same company is allowed to be excluded; similarity does not require a match.
            contacted = bool(
                link.instantly_lead_id
                or link.assessment_token
                or (link.outreach_status and link.outreach_status.value != "pending")
            )
            if not contacted:
                continue
            profile = profiles.get(link.id)
            qual = (
                profile.qualification_status
                if profile and profile.qualification_status is not None
                else QualificationStatus.PENDING
            )
            qualified = qual == QualificationStatus.QUALIFIED
            booked = (
                str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
                and link.booked_at is not None
            )
            purchased = link.purchase_outcome == PurchaseOutcome.PURCHASED
            revenue = _money(link.revenue_amount) if purchased else 0.0
            score, matched = self._similarity_score(target, other, link)
            if score <= 0:
                continue
            row = {
                "similarity": score,
                "matched_dimensions": matched,
                "industry": other.industry,
                "revenue_band": other.revenue_band,
                "headcount_band": other.headcount_band,
                "state": other.state,
                "region": other.region,
                "source": other.source,
                "buyer_title": other.executive_title,
                "buyer_title_bucket": _title_bucket(other.executive_title),
                "fit_band": _score_band(other.fit_score),
                "timing_band": _score_band(other.timing_score),
                "reachability_band": _score_band(other.reachability_score),
                "priority_band": _score_band(other.priority_score),
                "signal_categories": _signal_categories(other, link),
                "outcomes": {
                    "contacted": True,
                    "qualified": qualified,
                    "booked": booked,
                    "purchased": purchased,
                    "revenue": round(revenue, 2),
                },
            }
            prev = best_by_prospect.get(other.id)
            if prev is None or float(row["similarity"]) > float(prev["similarity"]):
                best_by_prospect[other.id] = row

        ranked = sorted(
            best_by_prospect.values(),
            key=lambda r: (
                -float(r["similarity"]),
                -int(bool(r["outcomes"]["purchased"])),
                -int(bool(r["outcomes"]["booked"])),
                -int(bool(r["outcomes"]["qualified"])),
                -float(r["outcomes"]["revenue"]),
            ),
        )
        return ranked[: max(1, int(limit))]

    @staticmethod
    def _similarity_score(
        target: dict[str, Any], other: Prospect, link: CampaignProspect
    ) -> tuple[float, list[str]]:
        score = 0.0
        matched: list[str] = []
        checks = (
            ("industry", _norm_key(other.industry), 3.0),
            ("revenue_band", _norm_key(other.revenue_band), 2.0),
            ("headcount_band", _norm_key(other.headcount_band), 1.5),
            ("state", _norm_key(other.state), 1.5),
            ("region", _norm_key(other.region), 1.0),
            ("source", _norm_key(other.source), 1.0),
            ("buyer_title", _title_bucket(other.executive_title), 1.5),
            ("fit_band", _score_band(other.fit_score), 1.0),
            ("timing_band", _score_band(other.timing_score), 1.0),
            ("reachability_band", _score_band(other.reachability_score), 0.75),
            ("priority_band", _score_band(other.priority_score), 0.75),
        )
        key_map = {
            "industry": "industry",
            "revenue_band": "revenue_band",
            "headcount_band": "headcount_band",
            "state": "state",
            "region": "region",
            "source": "source",
            "buyer_title": "title",
            "fit_band": "fit_band",
            "timing_band": "timing_band",
            "reachability_band": "reach_band",
            "priority_band": "priority_band",
        }
        for label, value, weight in checks:
            target_key = key_map[label]
            target_val = target.get(target_key)
            if (
                target_val
                and target_val != "unknown"
                and value
                and value != "unknown"
                and target_val == value
            ):
                score += weight
                matched.append(label)

        other_signals = set(_signal_categories(other, link))
        signal_overlap = target["signals"] & other_signals
        if signal_overlap:
            score += min(2.0, 0.75 * len(signal_overlap))
            matched.append("timing_signals")

        other_fit = {
            _norm_key(str(x), default="") for x in (other.fit_reasons or []) if x
        } - {""}
        fit_overlap = target["fit_themes"] & other_fit
        if fit_overlap:
            score += min(1.5, 0.5 * len(fit_overlap))
            matched.append("fit_themes")

        other_timing = {
            _norm_key(str(x), default="") for x in (other.timing_reasons or []) if x
        } - {""}
        timing_overlap = target["timing_themes"] & other_timing
        if timing_overlap:
            score += min(1.5, 0.5 * len(timing_overlap))
            if "timing_signals" not in matched:
                matched.append("timing_signals")

        return round(score, 3), matched

    @staticmethod
    def _summarize_comparable_outcomes(comparables: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(comparables)
        if n == 0:
            return {
                "sample": 0,
                "contacted": 0,
                "qualified": 0,
                "booked": 0,
                "purchased": 0,
                "revenue": 0.0,
                "qualified_rate": 0.0,
                "booked_rate": 0.0,
                "purchased_rate": 0.0,
            }
        qualified = sum(1 for c in comparables if c["outcomes"]["qualified"])
        booked = sum(1 for c in comparables if c["outcomes"]["booked"])
        purchased = sum(1 for c in comparables if c["outcomes"]["purchased"])
        revenue = sum(float(c["outcomes"]["revenue"] or 0) for c in comparables)
        return {
            "sample": n,
            "contacted": n,
            "qualified": qualified,
            "booked": booked,
            "purchased": purchased,
            "revenue": round(revenue, 2),
            "qualified_rate": round(qualified / n, 4),
            "booked_rate": round(booked / n, 4),
            "purchased_rate": round(purchased / n, 4),
        }

    def _relevant_cohorts(
        self, prospect: Prospect, summary: dict[str, Any], *, min_n: int
    ) -> dict[str, Any]:
        dims = summary.get("dimensions") or {}
        out: dict[str, Any] = {}

        def pick(dimension: str, key: str | None) -> None:
            k = _norm_key(key)
            bucket = (dims.get(dimension) or {}).get(k)
            if not bucket:
                return
            sample = int(bucket.get("sample") or 0)
            # Include small cohorts but mark them so the AI stays conservative.
            out.setdefault(dimension, {})[k] = {
                "sample": sample,
                "contacted": int(bucket.get("contacted") or 0),
                "qualified": int(bucket.get("qualified") or 0),
                "booked": int(bucket.get("booked") or 0),
                "purchased": int(bucket.get("purchased") or 0),
                "revenue": float(bucket.get("revenue") or 0),
                "qualified_rate": float(bucket.get("qualified_rate") or 0),
                "booked_rate": float(bucket.get("booked_rate") or 0),
                "purchased_rate": float(bucket.get("purchased_rate") or 0),
                "sufficient_sample": sample >= min_n,
            }

        pick("industry", prospect.industry)
        pick("revenue_band", prospect.revenue_band)
        pick("headcount_band", prospect.headcount_band)
        pick("state", prospect.state)
        pick("region", prospect.region)
        pick("source", prospect.source)
        pick("fit_band", _score_band(prospect.fit_score))
        pick("timing_band", _score_band(prospect.timing_score))
        pick("reachability_band", _score_band(prospect.reachability_score))
        for cat in _signal_categories(prospect, None)[:4]:
            pick("signal", cat)

        # Also include a few top performers (compact) for context.
        top = summary.get("top_performers") or {}
        out["top_performers"] = {
            key: (top.get(key) or [])[:3]
            for key in ("industries", "revenue_bands", "signals", "regions", "sources")
        }
        return out

    def _result_from_ai(
        self,
        *,
        base: float,
        ai_result: AiOutcomeLearningResult,
        max_adj: float,
        profile_version: int,
        historical_sample_size: int | None = None,
    ) -> dict[str, Any]:
        adjustment = _clamp(float(ai_result.adjustment), -abs(max_adj), abs(max_adj))
        if ai_result.insufficient_evidence:
            adjustment = 0.0
        adjustment = round(adjustment, 2)
        effective = round(_clamp(base + adjustment, 0.0, 100.0), 2)
        patterns = list(ai_result.matched_patterns or ai_result.supporting_patterns or [])
        reasons: list[str] = []
        if ai_result.reason:
            reasons.append(ai_result.reason)
        for pattern in patterns[:3]:
            dim = pattern.get("dimension") or "pattern"
            value = pattern.get("value") or ""
            obs = pattern.get("observation") or ""
            sample = pattern.get("sample")
            bits = [str(dim)]
            if value:
                bits.append(str(value))
            if obs:
                bits.append(str(obs))
            if sample is not None:
                bits.append(f"n={sample}")
            reasons.append(" · ".join(bits))
        if not reasons:
            reasons = ["No clear outcome pattern; leaving priority unchanged."]
        hist_n = (
            int(ai_result.historical_sample_size)
            if ai_result.historical_sample_size is not None
            else int(historical_sample_size or 0)
        )
        return {
            "enabled": True,
            "base_priority_score": base,
            "learning_adjustment": adjustment,
            "effective_priority_score": effective,
            "reasons": reasons[:5],
            "insufficient_data": bool(ai_result.insufficient_evidence),
            "confidence": ai_result.confidence,
            "model": ai_result.model,
            "prompt_version": ai_result.prompt_version or PROMPT_VERSION,
            "historical_sample_size": hist_n,
            "matched_patterns": patterns,
            "learning_profile_version": profile_version,
            "supporting_patterns": patterns,
            "openai_called": bool(ai_result.openai_called),
            "ok": bool(ai_result.ok),
            "error": ai_result.error,
            "cached": False,
        }

    @staticmethod
    def _neutral_result(
        *,
        base: float,
        enabled: bool,
        reasons: list[str],
        insufficient_data: bool,
        profile_version: int,
        openai_called: bool,
        error: str | None = None,
        model: str | None = None,
        historical_sample_size: int | None = None,
    ) -> dict[str, Any]:
        return {
            "enabled": enabled,
            "base_priority_score": base,
            "learning_adjustment": 0.0,
            "effective_priority_score": base,
            "reasons": reasons,
            "insufficient_data": insufficient_data,
            "confidence": 0.0,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "historical_sample_size": historical_sample_size,
            "matched_patterns": [],
            "learning_profile_version": profile_version,
            "supporting_patterns": [],
            "openai_called": openai_called,
            "ok": True,
            "error": error,
            "cached": False,
        }

    # ----- deterministic metrics (unchanged authority) -----

    def _compute_metrics(self) -> dict[str, Any]:
        links = list(
            self.db.scalars(
                select(CampaignProspect).options(
                    selectinload(CampaignProspect.prospect).selectinload(
                        Prospect.research_sources
                    ),
                    selectinload(CampaignProspect.campaign),
                )
            ).all()
        )
        profiles = {
            p.campaign_prospect_id: p
            for p in self.db.scalars(select(AssessmentProfile)).all()
        }
        all_prospects = list(self.db.scalars(select(Prospect)).all())

        totals = {
            "prospects_discovered": len(all_prospects),
            "contacted": 0,
            "assessment_started": 0,
            "assessment_completed": 0,
            "qualified": 0,
            "booked": 0,
            "showed": 0,
            "purchased": 0,
            "revenue": 0.0,
        }
        dimensions: dict[str, dict[str, dict[str, Any]]] = {
            "industry": defaultdict(_empty_bucket),
            "revenue_band": defaultdict(_empty_bucket),
            "headcount_band": defaultdict(_empty_bucket),
            "state": defaultdict(_empty_bucket),
            "region": defaultdict(_empty_bucket),
            "source": defaultdict(_empty_bucket),
            "fit_band": defaultdict(_empty_bucket),
            "timing_band": defaultdict(_empty_bucket),
            "reachability_band": defaultdict(_empty_bucket),
            "priority_band": defaultdict(_empty_bucket),
            "message_angle": defaultdict(_empty_bucket),
            "signal": defaultdict(_empty_bucket),
            "campaign": defaultdict(_empty_bucket),
        }
        high_fit_no_contact = 0
        high_fit_by_industry: dict[str, int] = defaultdict(int)
        seen_contacted: set[object] = set()

        for prospect in all_prospects:
            if (
                float(prospect.fit_score or 0) >= 70
                and (
                    prospect.acquisition_status == AcquisitionStatus.UNREACHABLE
                    or float(prospect.reachability_score or 0) < 20
                )
            ):
                high_fit_no_contact += 1
                high_fit_by_industry[_norm_key(prospect.industry)] += 1

        for link in links:
            prospect = link.prospect
            if prospect is None:
                continue
            contacted = bool(
                link.instantly_lead_id
                or link.assessment_token
                or (link.outreach_status and link.outreach_status.value != "pending")
            )
            if contacted and prospect.id not in seen_contacted:
                seen_contacted.add(prospect.id)
                totals["contacted"] += 1

            started = bool(
                link.assessment_started_at
                or link.assessment_status != AssessmentStatus.NOT_STARTED
            )
            completed = bool(
                link.assessment_completed_at
                or link.assessment_status == AssessmentStatus.COMPLETED
            )
            profile = profiles.get(link.id)
            qual = (
                profile.qualification_status
                if profile and profile.qualification_status is not None
                else QualificationStatus.PENDING
            )
            qualified = qual == QualificationStatus.QUALIFIED
            booked = (
                str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
                and link.booked_at is not None
            )
            showed = link.consultation_show_status == ConsultationShowStatus.SHOWED
            purchased = link.purchase_outcome == PurchaseOutcome.PURCHASED
            revenue = _money(link.revenue_amount) if purchased else 0.0

            if started:
                totals["assessment_started"] += 1
            if completed:
                totals["assessment_completed"] += 1
            if qualified:
                totals["qualified"] += 1
            if booked:
                totals["booked"] += 1
            if showed:
                totals["showed"] += 1
            if purchased:
                totals["purchased"] += 1
                totals["revenue"] += revenue

            keys = {
                "industry": _norm_key(prospect.industry),
                "revenue_band": _norm_key(prospect.revenue_band),
                "headcount_band": _norm_key(prospect.headcount_band),
                "state": _norm_key(prospect.state),
                "region": _norm_key(prospect.region),
                "source": _norm_key(prospect.source),
                "fit_band": _score_band(prospect.fit_score),
                "timing_band": _score_band(prospect.timing_score),
                "reachability_band": _score_band(prospect.reachability_score),
                "priority_band": _score_band(prospect.priority_score),
                "campaign": (
                    (link.campaign.name if link.campaign else None)
                    or str(link.campaign_id)
                ).strip().lower()
                or "unknown",
            }
            angle = _message_angle(link.outreach_message_key)
            if angle:
                keys["message_angle"] = _norm_key(angle)
            signal_cats = _signal_categories(prospect, link)

            def bump(dim: str, key: str) -> None:
                b = dimensions[dim][key]
                b["sample"] += 1
                if contacted:
                    b["contacted"] += 1
                if started:
                    b["assessment_started"] += 1
                if completed:
                    b["assessment_completed"] += 1
                if qualified:
                    b["qualified"] += 1
                if booked:
                    b["booked"] += 1
                if showed:
                    b["showed"] += 1
                if purchased:
                    b["purchased"] += 1
                    b["revenue"] += revenue

            for dim, key in keys.items():
                bump(dim, key)
            for cat in signal_cats:
                bump("signal", cat)

        contacted_n = max(1, totals["contacted"])
        baselines = {
            "qualified_rate": totals["qualified"] / contacted_n,
            "booked_rate": totals["booked"] / contacted_n,
            "purchased_rate": totals["purchased"] / contacted_n,
        }
        serialized_dims: dict[str, dict[str, Any]] = {}
        for dim, buckets in dimensions.items():
            out_buckets: dict[str, Any] = {}
            for key, b in buckets.items():
                n = max(1, int(b["sample"]))
                out_buckets[key] = {
                    **b,
                    "qualified_rate": b["qualified"] / n,
                    "booked_rate": b["booked"] / n,
                    "purchased_rate": b["purchased"] / n,
                    "revenue": round(float(b["revenue"]), 2),
                }
            serialized_dims[dim] = out_buckets

        min_n = int(self.icp.as_dict().get("learning_min_sample_size") or 25)
        top = {
            "industries": self._top_performers(serialized_dims.get("industry") or {}, min_n),
            "revenue_bands": self._top_performers(
                serialized_dims.get("revenue_band") or {}, min_n
            ),
            "signals": self._top_performers(serialized_dims.get("signal") or {}, min_n),
            "sources": self._top_performers(serialized_dims.get("source") or {}, min_n),
            "message_angles": self._top_performers(
                serialized_dims.get("message_angle") or {}, min_n
            ),
            "regions": self._top_performers(serialized_dims.get("region") or {}, min_n),
        }

        return {
            "totals": {**totals, "revenue": round(float(totals["revenue"]), 2)},
            "baselines": baselines,
            "dimensions": serialized_dims,
            "top_performers": top,
            "high_fit_no_contact": {
                "count": high_fit_no_contact,
                "by_industry": [
                    {"industry": k, "count": v}
                    for k, v in sorted(
                        high_fit_by_industry.items(), key=lambda x: (-x[1], x[0])
                    )
                ],
                "note": (
                    "High-fit prospects (fit≥70) failing due to missing/weak contact path."
                ),
            },
            "optimization_focus": [
                "qualified",
                "booked",
                "purchased",
                "revenue",
            ],
            "vanity_metrics_excluded": ["opens", "clicks"],
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _top_performers(
        buckets: dict[str, dict[str, Any]], min_n: int, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        ranked = []
        for key, b in buckets.items():
            n = int(b.get("sample") or 0)
            if n < min_n:
                continue
            ranked.append(
                {
                    "key": key,
                    "sample": n,
                    "qualified_rate": round(float(b.get("qualified_rate") or 0), 4),
                    "booked_rate": round(float(b.get("booked_rate") or 0), 4),
                    "purchased_rate": round(float(b.get("purchased_rate") or 0), 4),
                    "revenue": float(b.get("revenue") or 0),
                    "sufficient_sample": True,
                }
            )
        ranked.sort(
            key=lambda r: (
                -float(r["purchased_rate"]),
                -float(r["booked_rate"]),
                -float(r["qualified_rate"]),
                -int(r["sample"]),
                r["key"],
            )
        )
        return ranked[:limit]
