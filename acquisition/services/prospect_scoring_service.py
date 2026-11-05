"""Deterministic prospect scoring.

Operates only on structured data present on the Prospect plus Prospecting rules.
Does not invent research.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from acquisition.models.prospect import AcquisitionStatus, Prospect
from acquisition.services.prospecting_rules_service import (
    ProspectingRules,
    ProspectingRulesService,
    get_prospecting_rules,
)

from acquisition.services import acquisition_trace as acq_trace


_BAND_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*[mM]?")
_TIMING_KEYWORDS = (
    "hiring",
    "growth",
    "expand",
    "expansion",
    "acquisition",
    "acquired",
    "leadership",
    "new offering",
    "launch",
    "scaling",
    "scale",
    "funding",
    "raised",
    "technology",
    "digital transformation",
    "ops pressure",
    "operational",
)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


DEFAULT_SCORE_WEIGHTS: dict[str, Any] = {
    "fit": 0.45,
    "timing": 0.25,
    "reachability": 0.30,
    "ready_for_outreach_min_priority": 55,
    "ready_for_outreach_min_reachability": 40,
    "allow_generic_inbox": False,
    "outreach_channel": "email",
}


def _load_rules(db: Session) -> ProspectingRules:
    try:
        return ProspectingRulesService(db).current()
    except Exception:  # noqa: BLE001
        return get_prospecting_rules()


def _dollars_to_millions(value: int) -> float:
    return float(value) / 1_000_000.0


def _targeting_cfg(rules: ProspectingRules) -> dict[str, Any]:
    country = (rules.country or "").strip() or "United States"
    nationwide = country.casefold() in {"united states", "usa", "us"}
    keywords: list[str] = []
    names: list[str] = []
    for cat in rules.categories or []:
        if cat.name:
            names.append(cat.name)
        keywords.extend(str(k) for k in (cat.keywords or []) if k)
    return {
        "geography": {
            "countries": [country],
            "nationwide": nationwide,
            "states": [],
            "cities": [],
        },
        "revenue": {
            "primary_min_millions": _dollars_to_millions(rules.revenue_min),
            "primary_max_millions": _dollars_to_millions(rules.revenue_max),
            "sweet_spot_min_millions": _dollars_to_millions(rules.revenue_sweet_min),
            "sweet_spot_max_millions": _dollars_to_millions(rules.revenue_sweet_max),
            "allow_strong_fit_outside_band": bool(rules.revenue_is_soft),
        },
        "buyer_titles": list(rules.titles or []),
        "priority_categories": names,
        "fit_themes": keywords,
        "excluded_categories": list(rules.exclusions or []),
        "score_weights": dict(DEFAULT_SCORE_WEIGHTS),
        "employees_min": int(rules.employees_min),
        "employees_max": int(rules.employees_max),
        "require_verified_email": bool(rules.require_verified_email),
    }


def _parse_headcount(band: str | None) -> float | None:
    if not band:
        return None
    nums = [float(m.group(1)) for m in re.finditer(r"(\d+(?:\.\d+)?)", str(band).replace(",", ""))]
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    return (nums[0] + nums[1]) / 2.0


def _parse_revenue_millions(band: str | None) -> float | None:
    if not band:
        return None
    text = band.lower().replace(",", "")
    # Prefer mid of range like $5–10M or 5-10M
    nums = [float(m.group(1)) for m in _BAND_NUM.finditer(text)]
    if not nums:
        return None
    # Heuristic: values under 1000 treated as millions already; 5000+ as dollars → /1e6
    cleaned = []
    for n in nums:
        cleaned.append(n / 1_000_000 if n >= 1000 else n)
    if len(cleaned) == 1:
        return cleaned[0]
    return (cleaned[0] + cleaned[1]) / 2.0


def _text_blob(prospect: Prospect) -> str:
    parts = [
        prospect.industry or "",
        prospect.company_description or "",
        prospect.fit_note or "",
        prospect.research_notes or "",
        prospect.executive_title or "",
        prospect.authority_type or "",
    ]
    if isinstance(prospect.public_signals, list):
        parts.extend(str(x) for x in prospect.public_signals)
    elif isinstance(prospect.public_signals, dict):
        parts.extend(f"{k} {v}" for k, v in prospect.public_signals.items())
    return " ".join(parts).lower()


class ProspectScoringService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def score_prospect(self, prospect: Prospect, *, persist: bool = True) -> dict[str, Any]:
        rules = _load_rules(self.db)
        cfg = _targeting_cfg(rules)
        fit = self._score_fit(prospect, cfg)
        timing = self._score_timing(prospect, cfg)
        reach = self._score_reachability(prospect, cfg)
        weights = cfg.get("score_weights") or {}
        w_fit = float(weights.get("fit", 0.45))
        w_timing = float(weights.get("timing", 0.25))
        w_reach = float(weights.get("reachability", 0.30))
        total_w = w_fit + w_timing + w_reach or 1.0
        priority = _clamp(
            (fit["score"] * w_fit + timing["score"] * w_timing + reach["score"] * w_reach)
            / total_w
        )
        acq_trace.trace_score_fit(score=float(fit["score"]), reasons=list(fit["reasons"] or []))
        timing_signals: list[str] = []
        blob = _text_blob(prospect)
        for kw in _TIMING_KEYWORDS:
            if kw in blob:
                timing_signals.append(kw)
        acq_trace.trace_score_timing(
            score=float(timing["score"]),
            reasons=list(timing["reasons"] or []),
            signals=timing_signals,
        )
        acq_trace.trace_score_reachability(
            score=float(reach["score"]), reasons=list(reach["reasons"] or [])
        )
        acq_trace.trace_score_priority(
            fit=float(fit["score"]),
            timing=float(timing["score"]),
            reach=float(reach["score"]),
            w_fit=w_fit,
            w_timing=w_timing,
            w_reach=w_reach,
            base_priority=round(priority, 2),
        )
        priority_reasons = [
            f"Weighted blend fit={fit['score']:.0f} timing={timing['score']:.0f} "
            f"reachability={reach['score']:.0f}",
            *fit["reasons"][:2],
            *reach["reasons"][:1],
        ]

        from acquisition.services.prospect_identity import is_placeholder_email

        confidence = _clamp(
            (
                (0.35 if prospect.industry else 0.0)
                + (0.2 if prospect.revenue_band else 0.0)
                + (0.15 if prospect.executive_title else 0.0)
                + (
                    0.2
                    if (prospect.email or "").strip()
                    and (prospect.email_confidence or "").strip().lower()
                    not in {"placeholder", "none", "missing"}
                    and not is_placeholder_email(prospect.email)
                    else 0.0
                )
                + (0.1 if prospect.domain or prospect.website else 0.0)
            )
            * 100
        )

        excluded = fit.get("excluded", False)
        ready_min_priority = float(weights.get("ready_for_outreach_min_priority", 55))
        ready_min_reach = float(weights.get("ready_for_outreach_min_reachability", 40))

        has_legit_email = bool((prospect.email or "").strip()) and not is_placeholder_email(
            prospect.email
        )
        conf_label = (prospect.email_confidence or "").strip().lower()
        if conf_label in {"placeholder", "none", "missing"}:
            has_legit_email = False

        if excluded:
            status = AcquisitionStatus.EXCLUDED
        elif reach["score"] < 20 or not has_legit_email:
            status = AcquisitionStatus.UNREACHABLE
        elif priority >= ready_min_priority and reach["score"] >= ready_min_reach:
            status = AcquisitionStatus.READY_FOR_OUTREACH
            if cfg.get("require_verified_email") and conf_label not in {
                "high",
                "validated",
                "verified",
            }:
                status = AcquisitionStatus.SCORED
        elif fit["score"] < 35 and confidence < 40:
            status = AcquisitionStatus.NEEDS_MORE_RESEARCH
        else:
            status = AcquisitionStatus.SCORED

        # Also map sparse researched prospects with weak confidence to needs_more_research.
        if (
            status == AcquisitionStatus.SCORED
            and confidence < 35
            and not prospect.industry
            and not prospect.revenue_band
        ):
            status = AcquisitionStatus.NEEDS_MORE_RESEARCH

        result = {
            "fit": fit,
            "timing": timing,
            "reachability": reach,
            "priority": {"score": round(priority, 2), "reasons": priority_reasons},
            "confidence": round(confidence, 2),
            "acquisition_status": status.value,
            "excluded": excluded,
        }

        if persist:
            prospect.fit_score = fit["score"]
            prospect.fit_reasons = fit["reasons"]
            prospect.timing_score = timing["score"]
            prospect.timing_reasons = timing["reasons"]
            prospect.reachability_score = reach["score"]
            prospect.reachability_reasons = reach["reasons"]
            prospect.priority_score = round(priority, 2)
            prospect.priority_reasons = priority_reasons
            prospect.score_confidence = round(confidence, 2)
            prospect.scored_at = datetime.now(timezone.utc)
            # Do not downgrade approval; only advance acquisition_status when scoring.
            prospect.acquisition_status = status
            prospect.effective_priority_score = round(priority, 2)
            self.db.add(prospect)
            self.db.commit()
            self.db.refresh(prospect)
        else:
            prospect.effective_priority_score = round(priority, 2)

        result["effective_priority"] = {
            "score": round(priority, 2),
            "adjustment": 0.0,
            "reasons": [],
        }

        return result

    def _score_fit(self, prospect: Prospect, cfg: dict[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        score = 45.0  # neutral baseline — missing optional fields do not auto-fail
        blob = _text_blob(prospect)
        industry = (prospect.industry or "").lower()

        excluded_cats = [str(x).lower() for x in (cfg.get("excluded_categories") or [])]
        classification = prospect.classification_json if isinstance(prospect.classification_json, dict) else None
        if classification is not None:
            # AI classification of what the firm primarily IS takes precedence over
            # keyword matching (an agency that *serves* contractors is not excluded).
            hit = str(classification.get("excluded_match") or "").strip()
            btype = str(classification.get("business_type") or prospect.business_type or "").lower()
            if not hit:
                for cat in excluded_cats:
                    if cat and re.search(rf"\b{re.escape(cat)}s?\b", btype):
                        hit = cat
                        break
            if hit:
                return {
                    "score": 5.0,
                    "reasons": [f"Business type is excluded: {hit} (AI classification: {btype or 'n/a'})"],
                    "excluded": True,
                }
        else:
            for cat in excluded_cats:
                if cat and (cat in industry or cat in blob):
                    return {
                        "score": 5.0,
                        "reasons": [f"Matches excluded category: {cat}"],
                        "excluded": True,
                    }
        if prospect.business_type:
            blob = f"{blob} {prospect.business_type.lower()}"
            industry = f"{industry} {prospect.business_type.lower()}"

        # Geography: US nationwide default — do not require Chicago/state.
        geo = cfg.get("geography") or {}
        if geo.get("nationwide") or "United States" in (geo.get("countries") or []):
            # Soft positive if US markers present; no penalty if unknown.
            if any(tok in blob for tok in ("united states", " usa", "u.s.", " us ")):
                score += 5
                reasons.append("US geography signal present")
            elif prospect.state or prospect.region or prospect.city:
                score += 3
                reasons.append("Domestic location fields present")
            else:
                reasons.append("Geography not yet confirmed (not disqualifying)")

        # Revenue band vs ICP
        rev_cfg = cfg.get("revenue") or {}
        millions = _parse_revenue_millions(prospect.revenue_band)
        if millions is None:
            reasons.append("Revenue band unknown (not disqualifying)")
        else:
            pmin = float(rev_cfg.get("primary_min_millions", 5))
            pmax = float(rev_cfg.get("primary_max_millions", 50))
            smin = float(rev_cfg.get("sweet_spot_min_millions", 7))
            smax = float(rev_cfg.get("sweet_spot_max_millions", 30))
            if smin <= millions <= smax:
                score += 25
                reasons.append(f"Revenue ~${millions:.0f}M in sweet spot")
            elif pmin <= millions <= pmax:
                score += 15
                reasons.append(f"Revenue ~${millions:.0f}M in primary band")
            elif rev_cfg.get("allow_strong_fit_outside_band"):
                score -= 5
                reasons.append(f"Revenue ~${millions:.0f}M outside primary band (soft)")
            else:
                score -= 20
                reasons.append(f"Revenue ~${millions:.0f}M outside ICP band")

        # Buyer / authority
        titles = [str(t).lower() for t in (cfg.get("buyer_titles") or [])]
        title = (prospect.executive_title or "").lower()
        if prospect.is_founder_or_equivalent:
            score += 18
            reasons.append("Marked founder/CEO-equivalent authority")
        elif any(t in title for t in titles if t):
            score += 15
            reasons.append(f"Title matches buyer ICP ({prospect.executive_title})")
        elif title:
            score += 4
            reasons.append("Title present but not clear highest-authority match")
        else:
            reasons.append("Decision-maker title unknown (not disqualifying)")

        headcount = _parse_headcount(prospect.headcount_band)
        emp_min = int(cfg.get("employees_min") or 0)
        emp_max = int(cfg.get("employees_max") or 0)
        if headcount is None:
            reasons.append("Headcount unknown (not disqualifying)")
        elif emp_min or emp_max:
            hi = emp_max if emp_max else headcount
            lo = emp_min
            if lo <= headcount <= hi:
                score += 10
                reasons.append(f"Headcount ~{headcount:.0f} in range")
            else:
                score -= 8
                reasons.append(f"Headcount ~{headcount:.0f} outside {lo}–{hi}")

        # Priority categories boost
        for cat in cfg.get("priority_categories") or []:
            c = str(cat).lower()
            if c and (c in industry or c in blob):
                score += 12
                reasons.append(f"Priority category signal: {cat}")
                break

        # Fit themes soft boost
        theme_hits = 0
        for theme in cfg.get("fit_themes") or []:
            # match on meaningful tokens from theme
            tokens = [tok for tok in re.split(r"[^a-z0-9]+", str(theme).lower()) if len(tok) > 4]
            if tokens and sum(1 for tok in tokens if tok in blob) >= max(1, len(tokens) // 3):
                theme_hits += 1
        if theme_hits:
            score += min(12, theme_hits * 4)
            reasons.append(f"Fit theme overlap ({theme_hits})")

        return {"score": round(_clamp(score), 2), "reasons": reasons or ["Limited fit evidence"], "excluded": False}

    def _score_timing(self, prospect: Prospect, cfg: dict[str, Any]) -> dict[str, Any]:
        del cfg  # reserved for future tunable timing dictionaries
        reasons: list[str] = []
        blob = _text_blob(prospect)
        if prospect.last_researched_at is not None and isinstance(prospect.public_signals, list):
            # a researched firm: only the recent changes found count, not plain words in its description
            blob = " ".join(str(x) for x in prospect.public_signals).lower()
        hits = [kw for kw in _TIMING_KEYWORDS if kw in blob]
        if not hits:
            return {
                "score": 35.0,
                "reasons": ["No public timing signals on record yet (neutral)"],
            }
        score = 40 + min(45, len(hits) * 8)
        reasons.append("Timing signals: " + ", ".join(hits[:6]))
        return {"score": round(_clamp(score), 2), "reasons": reasons}

    def _score_reachability(self, prospect: Prospect, cfg: dict[str, Any]) -> dict[str, Any]:
        del cfg
        reasons: list[str] = []
        score = 0.0
        email = (prospect.email or "").strip()
        conf = (prospect.email_confidence or "").strip().lower()
        from acquisition.services.prospect_identity import is_placeholder_email

        if not email or is_placeholder_email(email) or conf in {"placeholder", "none", "missing"}:
            return {
                "score": 0.0,
                "reasons": ["No legitimate business email on record — unreachable for outreach"],
            }
        score += 50
        reasons.append("Business email present")
        if conf in {"high", "validated", "verified"}:
            score += 30
            reasons.append(f"Email confidence: {conf}")
        elif conf in {"medium", "probable"}:
            score += 15
            reasons.append(f"Email confidence: {conf}")
        elif conf in {"low", "unverified", "catch_all", "inferred"}:
            score += 5
            reasons.append(f"Email confidence: {conf}")
        else:
            score += 10
            reasons.append("Email confidence not set (assumed medium)")
        if (prospect.company_phone or "").strip():
            score += 10
            reasons.append("Company phone available")
        if (prospect.other_contact_path or "").strip():
            score += 5
            reasons.append("Alternate business contact path present")
        if (prospect.linkedin_url or "").strip():
            score += 5
            reasons.append("Public professional profile URL present")
        return {"score": round(_clamp(score), 2), "reasons": reasons}
