"""Complete assessment: structured turn completion, grounded results, AI qualification."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from acquisition.models.assessment import AssessmentConversation, AssessmentProfile
from acquisition.models.campaign import CampaignProspect, JourneyStage
from acquisition.models.journey import (
    STRUCTURED_FIELD_KEYS,
    QualificationStatus,
    empty_qualification,
)
from acquisition.models.settings import (
    DEFAULT_QUALIFICATION_SYSTEM_PROMPT,
    AIReplySettings,
)
from acquisition.services.assessment_extraction import (
    AUTHORITY_ROLES,
    backend_coverage_satisfied,
    evidence_quote_in_user_message,
    field_display,
    is_ready_for_completion,
    is_resolved,
    normalize_structured_state,
    parse_extractor_json,
)
from acquisition.services.assessment_service import AssessmentEventService

logger = logging.getLogger(__name__)

RESULT_INSTRUCTIONS = """
You write the prospect-facing Assessment Result for FreedomBot (CAIO).
Based ONLY on the structured assessment state and USER messages, return JSON:
{
  "inefficiencies_summary": "<one short personalized paragraph>",
  "opportunity_areas": [
    {
      "label": "<short business outcome/capability>",
      "evidence_quotes": ["<exact quote from a USER message>"],
      "based_on_fields": ["<structured field keys that support this point>"]
    }
  ],
  "observed_impact": "<short impact statement grounded in evidence, or null>",
  "impact_kind": "observed|directional_financial|null",
  "directional_impact": "<legacy alias of observed_impact; same value or null>"
}

Structure the content for these four sections (in this order):
1) Possible inefficiencies
2) Where FreedomBot may help
3) Observed impact
4) Next step (handled by the product UI — do not invent CTAs)

POSSIBLE INEFFICIENCIES (inefficiencies_summary):
- Write ONE short paragraph in natural, personalized business language.
- Explain the main operational problem and likely root cause from THIS prospect's evidence.
- Prefer second-person / "your" wording when natural (e.g. "Your sales-to-delivery handoff…").
- Use cautious wording where appropriate: "may be causing", "appears to be", "could be contributing to".
- Do NOT present a definitive diagnosis.
- Do NOT use generic filler or robotic passive phrasing like "information is being transferred…".
- Do NOT invent facts not supported by the assessment.

WHERE FREEDOMBOT MAY HELP (opportunity_areas):
- Return 2–4 short points (labels only need to be outcome-focused).
- Each point is a business outcome/capability, NOT technical architecture.
- Example style (do not copy unless evidence supports):
  - More consistent sales-to-delivery handoffs
  - Centralized access to pricing and quality guidance
  - Less owner dependency for routine project decisions
- Every area MUST include evidence_quotes from USER messages and based_on_fields that are resolved.
- Do NOT inject generic FreedomBot feature menus unrelated to this assessment.
- Do NOT write implementation plans, architecture, automation sequences, or configuration steps.

OBSERVED IMPACT (observed_impact / impact_kind):
- Only use numbers actually supported by assessment evidence (e.g. known time_cost_impact).
- If time/delay/rework was given, state that plainly.
- Only include a dollar estimate when enough reliable cost information exists in the evidence.
- Never invent financial savings or guaranteed ROI.
- impact_kind = "observed" when you only have operational/time evidence.
- impact_kind = "directional_financial" ONLY when a defensible financial estimate exists in evidence.
- If no supported impact: observed_impact=null and impact_kind=null.

Tone: concise, human, diagnostic, non-salesy. Keep the full result short.
Return JSON only.
""".strip()

# Canonical default lives in AI settings; keep this alias for tests / imports.
QUALIFICATION_INSTRUCTIONS = DEFAULT_QUALIFICATION_SYSTEM_PROMPT

_ICP = frozenset({"strong", "moderate", "weak", "unknown"})
_COMPLEXITY = frozenset({"low", "medium", "high", "unknown"})
_READINESS = frozenset({"high", "medium", "low", "unknown"})
_ECONOMIC = frozenset({"likely", "unclear", "unlikely"})
_BUDGET = frozenset({"not_assessed", "ready", "not_ready", "unclear"})
_STATUS = frozenset(
    {
        QualificationStatus.QUALIFIED.value,
        QualificationStatus.UNQUALIFIED.value,
        QualificationStatus.PENDING.value,
    }
)


def normalize_assessment_result(
    parsed: dict[str, Any] | None,
    *,
    state: dict[str, Any] | None,
    user_messages: list[str] | None = None,
    conversation_text: str = "",  # unused; retained for call-site compat
) -> dict[str, Any]:
    """Sanitize model result — drop unevidenced opportunity areas; no capability menu."""
    del conversation_text
    data = parsed if isinstance(parsed, dict) else {}
    norm_state = normalize_structured_state(state)
    user_blobs = [m for m in (user_messages or []) if isinstance(m, str) and m.strip()]

    summary = data.get("inefficiencies_summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = (
            "Based on what you shared, there may be operational friction worth reviewing "
            "with a CAIO lens."
        )
    else:
        summary = summary.strip()

    # Prefer observed_impact; keep directional_impact as legacy alias.
    impact_raw = data.get("observed_impact")
    if impact_raw is None:
        impact_raw = data.get("directional_impact")
    if isinstance(impact_raw, str):
        impact = impact_raw.strip() or None
    else:
        impact = None

    # Impact only when assessment has known time_cost_impact (no invented numbers).
    tci = field_display(norm_state.get("time_cost_impact"))
    if impact and tci.get("status") != "known":
        impact = None

    impact_kind = str(data.get("impact_kind") or "").strip().lower() or None
    if impact is None:
        impact_kind = None
    elif impact_kind not in {"observed", "directional_financial"}:
        # Default: operational/time evidence ⇒ Observed impact.
        impact_kind = "observed"
    if impact_kind == "directional_financial":
        # Without explicit dollar evidence in the impact text + known time/cost field,
        # do not claim directional financial impact.
        if "$" not in (impact or "") and "dollar" not in (impact or "").lower():
            impact_kind = "observed"

    impact_label = None
    if impact:
        impact_label = (
            "Directional financial impact"
            if impact_kind == "directional_financial"
            else "Observed impact"
        )

    raw_areas = data.get("opportunity_areas")
    if not isinstance(raw_areas, list):
        raw_areas = []

    areas: list[dict[str, Any]] = []
    for item in raw_areas:
        validated = _validate_opportunity_area(item, norm_state, user_blobs)
        if validated is not None:
            areas.append(validated)
    # Prospect-facing result expects 2–4 help points when evidence exists.
    if len(areas) > 4:
        areas = areas[:4]

    opportunity_map = "; ".join(a["label"] for a in areas) if areas else None

    return {
        "inefficiencies_summary": summary,
        "observed_impact": impact,
        "impact_kind": impact_kind,
        "impact_label": impact_label,
        # Legacy alias used by older templates/tests.
        "directional_impact": impact,
        "opportunity_areas": areas,
        "opportunity_map": opportunity_map,
        "next_step_qualified": (
            "See relevant examples of how similar operational problems can be addressed."
        ),
        "next_step_unqualified": (
            "Thanks for completing the assessment. We’ve saved your snapshot."
        ),
    }


def _validate_opportunity_area(
    item: Any,
    state: dict[str, Any],
    user_messages: list[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    label = item.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    quotes = item.get("evidence_quotes")
    if not isinstance(quotes, list) or not quotes:
        return None
    grounded: list[str] = []
    for q in quotes:
        if not isinstance(q, str) or not q.strip():
            continue
        if any(evidence_quote_in_user_message(q, msg) for msg in user_messages):
            grounded.append(q.strip())
    if not grounded:
        logger.info("Dropped opportunity area %r: no grounded user evidence", label)
        return None

    fields = item.get("based_on_fields")
    if not isinstance(fields, list) or not fields:
        return None
    supported: list[str] = []
    for key in fields:
        if not isinstance(key, str):
            continue
        key_norm = key.strip()
        if key_norm not in STRUCTURED_FIELD_KEYS:
            continue
        if is_resolved(state.get(key_norm)):
            supported.append(key_norm)
    if not supported:
        logger.info("Dropped opportunity area %r: no supported based_on_fields", label)
        return None

    return {
        "label": label.strip(),
        "evidence_quotes": grounded,
        "based_on_fields": supported,
    }


def validate_qualification_payload(
    parsed: dict[str, Any] | None,
    *,
    user_messages: list[str],
    force_assessment_budget_gate: bool = True,
) -> dict[str, Any]:
    """Validate qualifier JSON; unqualified without grounded disqualifiers → pending.

    Assessment-time qualified means eligible for Result → Proof (not calendar-ready).
    When ``force_assessment_budget_gate``, ``budget_readiness`` is always ``not_assessed``.
    """
    base = empty_qualification()
    if not isinstance(parsed, dict):
        base["explanation"] = "Qualification metadata missing; pending."
        return base

    status = str(
        parsed.get("assessment_status") or parsed.get("status") or ""
    ).strip().lower()
    if status not in _STATUS:
        status = QualificationStatus.PENDING.value

    def _enum(key: str, allowed: frozenset[str], default: str = "unknown") -> str:
        raw = parsed.get(key)
        if raw is None:
            return default
        val = str(raw).strip().lower()
        return val if val in allowed else default

    authority = parsed.get("authority_role")
    if authority is None:
        authority = parsed.get("authority")
    authority_norm = str(authority or "unknown").strip().lower().replace(" ", "_").replace("-", "_")
    if authority_norm not in AUTHORITY_ROLES:
        authority_norm = "unknown"

    icp = _enum("icp_fit", _ICP)
    complexity = _enum("complexity", _COMPLEXITY)
    readiness = _enum("growth_change_readiness", _READINESS)
    openness = _enum("openness_to_ai", _READINESS)

    # Prefer economic_capacity; legacy ability_to_pay maps only into capacity (not budget).
    economic_raw = parsed.get("economic_capacity")
    if economic_raw is None:
        economic_raw = parsed.get("ability_to_pay")
    economic = "unclear"
    if economic_raw is not None:
        economic_val = str(economic_raw).strip().lower()
        if economic_val in _ECONOMIC:
            economic = economic_val
        elif economic_val == "unknown":
            economic = "unclear"

    budget = "not_assessed"
    if not force_assessment_budget_gate:
        budget = _enum("budget_readiness", _BUDGET, default="not_assessed")
    elif parsed.get("budget_readiness") not in (None, "not_assessed"):
        logger.info(
            "Ignoring assessment-time budget_readiness=%r; forcing not_assessed",
            parsed.get("budget_readiness"),
        )

    money = parsed.get("real_money_problem")
    if money is not None and not isinstance(money, bool):
        money = None

    disqualifiers: list[dict[str, str]] = []
    raw_dq = parsed.get("disqualifiers")
    if isinstance(raw_dq, list):
        for item in raw_dq:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            quote = item.get("evidence_quote")
            if not isinstance(code, str) or not code.strip():
                continue
            if not isinstance(quote, str) or not quote.strip():
                continue
            if not any(evidence_quote_in_user_message(quote, msg) for msg in user_messages):
                logger.info("Dropped disqualifier %r: evidence not in user messages", code)
                continue
            disqualifiers.append({"code": code.strip(), "evidence_quote": quote.strip()})

    explanation = parsed.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = "Structured qualification completed."

    signal_reasons: dict[str, str] = {}
    raw_reasons = parsed.get("signal_reasons")
    if isinstance(raw_reasons, dict):
        for key, value in raw_reasons.items():
            if not isinstance(key, str) or not key.strip():
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            signal_reasons[key.strip()] = value.strip()

    if status == QualificationStatus.UNQUALIFIED.value and not disqualifiers:
        logger.warning(
            "Qualifier returned unqualified without grounded disqualifiers; downgrading to pending"
        )
        status = QualificationStatus.PENDING.value
        explanation = (
            "Insufficient negative evidence for unqualified — missing or unsupported "
            "disqualifiers; pending."
        )

    # Grounded disqualifiers cannot coexist with assessment-qualified.
    if disqualifiers and status == QualificationStatus.QUALIFIED.value:
        status = QualificationStatus.UNQUALIFIED.value
        if explanation == "Structured qualification completed.":
            explanation = "Disqualifying evidence present; marked unqualified."

    result = {
        "status": status,
        "assessment_status": status,
        "icp_fit": icp,
        "complexity": complexity,
        "real_money_problem": money,
        "authority_role": authority_norm,
        # UI/compat key mirrors authority_role (enum, never free-text → decision_maker).
        "authority": authority_norm,
        "growth_change_readiness": readiness,
        "openness_to_ai": openness,
        "economic_capacity": economic,
        "budget_readiness": budget,
        # Legacy mirror of economic_capacity only — not confirmed ability-to-pay / budget.
        "ability_to_pay": economic,
        "disqualifiers": disqualifiers,
        "signal_reasons": signal_reasons,
        "explanation": explanation.strip(),
        "notes": None,
    }
    return apply_documented_qualification_rules(result)


def documented_qualification_criteria_met(qualification: dict[str, Any]) -> bool:
    """True when documented business signals support assessment-qualified.

    Explicit willingness to continue to proof/demo/value is intentionally NOT required.
    """
    if not isinstance(qualification, dict):
        return False
    if qualification.get("disqualifiers"):
        return False
    icp = str(qualification.get("icp_fit") or "").strip().lower()
    complexity = str(qualification.get("complexity") or "").strip().lower()
    authority = str(
        qualification.get("authority_role") or qualification.get("authority") or ""
    ).strip().lower()
    readiness = str(qualification.get("growth_change_readiness") or "").strip().lower()
    openness = str(qualification.get("openness_to_ai") or "").strip().lower()
    economic = str(
        qualification.get("economic_capacity") or qualification.get("ability_to_pay") or ""
    ).strip().lower()
    money = qualification.get("real_money_problem")
    return (
        icp in {"strong", "moderate"}
        and complexity in {"medium", "high"}
        and money is True
        and authority in {"decision_maker", "influencer"}
        and readiness in {"high", "medium"}
        and openness in {"high", "medium"}
        and economic == "likely"
    )


def apply_documented_qualification_rules(qualification: dict[str, Any]) -> dict[str, Any]:
    """Promote pending → qualified when documented criteria are met.

    Prevents LLMs from blocking qualification solely because the prospect did not
    verbally ask for proof/demo/value.
    """
    result = dict(qualification)
    status = str(
        result.get("assessment_status") or result.get("status") or ""
    ).strip().lower()
    if status != QualificationStatus.PENDING.value:
        result["status"] = status if status in _STATUS else QualificationStatus.PENDING.value
        result["assessment_status"] = result["status"]
        return result
    if not documented_qualification_criteria_met(result):
        return result

    result["status"] = QualificationStatus.QUALIFIED.value
    result["assessment_status"] = QualificationStatus.QUALIFIED.value
    explanation = str(result.get("explanation") or "").strip()
    lower = explanation.lower()
    if any(
        marker in lower
        for marker in (
            "continue to a proof",
            "continue to proof",
            "willingness to continue",
            "explicit willingness",
            "proof, demo, or value",
            "proof/demo/value",
        )
    ):
        explanation = (
            "Assessment-qualified on documented business criteria (ICP, complexity, "
            "operating problem, authority, change readiness, AI openness, economic "
            "capacity). Explicit willingness to continue to proof/demo/value is not required."
        )
    elif explanation:
        explanation = (
            f"{explanation} Confirmed assessment-qualified on documented business "
            "criteria; continuation language is not required."
        )
    else:
        explanation = (
            "Assessment-qualified on documented business criteria."
        )
    result["explanation"] = explanation
    logger.info(
        "Promoted pending → qualified: documented business criteria met "
        "(continuation language is not a gate)"
    )
    return result


def is_calendar_eligible(
    *,
    qualification: dict[str, Any] | None = None,
    qualification_status: str | QualificationStatus | None = None,
    proof_demo_completed: bool = False,
    value_price_conditioning_completed: bool = False,
    link: Any | None = None,
    profile: Any | None = None,
) -> bool:
    """Jeanne calendar unlock gate.

    Prefer ``link`` + ``profile`` (full acquisition gates). Legacy kwargs still
    work for unit tests of qualification-only semantics.
    """
    if link is not None:
        from acquisition.services.acquisition_journey_service import (
            is_calendar_eligible as _full_gate,
        )

        return _full_gate(link=link, profile=profile)

    qual = qualification if isinstance(qualification, dict) else {}
    status_raw = qualification_status
    if status_raw is None:
        status_raw = qual.get("assessment_status") or qual.get("status")
    if isinstance(status_raw, QualificationStatus):
        status_norm = status_raw.value
    else:
        status_norm = str(status_raw or "").strip().lower()
    if status_norm != QualificationStatus.QUALIFIED.value:
        return False
    if not proof_demo_completed or not value_price_conditioning_completed:
        return False
    budget = str(qual.get("budget_readiness") or "").strip().lower()
    return budget == "ready"


def humanize_disqualifier(code: str, item: dict[str, Any] | None = None) -> tuple[str, str]:
    """Map disqualifier codes to Admin labels (Change readiness / Not ready)."""
    item = item if isinstance(item, dict) else {}
    presets = {
        "not_willing_to_change": ("Change readiness", "Not ready"),
        "unwilling_to_change": ("Change readiness", "Not ready"),
        "low_change_readiness": ("Change readiness", "Not ready"),
        "not_open_to_ai": ("AI openness", "Not ready"),
        "low_ai_openness": ("AI openness", "Not ready"),
        "no_authority": ("Authority", "Not confirmed"),
        "not_decision_maker": ("Authority", "Not decision maker"),
        "weak_icp": ("Company fit", "Weak"),
        "no_money_problem": ("Real operating problem", "No"),
        "low_complexity": ("Operational complexity", "Low"),
    }
    if code in presets:
        return presets[code]
    label = str(item.get("label") or "").strip()
    factor = str(item.get("factor_value") or item.get("summary") or "").strip()
    if label and factor:
        return label, factor
    pretty = code.replace("_", " ").strip().title() or "Key factor"
    return pretty, "Noted"


def build_qualification_transparency(
    qualification: dict[str, Any] | None,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    """Admin display helper: status + breakdown + explicit why (no numeric score)."""
    qual = qualification if isinstance(qualification, dict) else {}
    status_norm = str(
        status
        or qual.get("assessment_status")
        or qual.get("status")
        or QualificationStatus.PENDING.value
    ).strip().lower()
    if status_norm not in _STATUS:
        status_norm = QualificationStatus.PENDING.value

    dimensions = [
        ("icp_fit", "Company fit"),
        ("complexity", "Operational complexity"),
        ("real_money_problem", "Real operating problem"),
        ("authority_role", "Authority"),
        ("growth_change_readiness", "Change readiness"),
        ("openness_to_ai", "AI openness"),
        ("economic_capacity", "Scale / economic capacity"),
        ("budget_readiness", "Budget readiness"),
    ]

    reasons_raw = qual.get("signal_reasons")
    reasons = reasons_raw if isinstance(reasons_raw, dict) else {}

    # Map disqualifier codes → dimension keys so evidence stays in the same row deck.
    dq_by_dimension: dict[str, str] = {}
    for item in qual.get("disqualifiers") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        quote = item.get("evidence_quote")
        if not (isinstance(code, str) and code.strip() and isinstance(quote, str) and quote.strip()):
            continue
        label, _factor = humanize_disqualifier(code.strip(), item)
        dim_key = {
            "Company fit": "icp_fit",
            "Operational complexity": "complexity",
            "Real operating problem": "real_money_problem",
            "Authority": "authority_role",
            "Change readiness": "growth_change_readiness",
            "AI openness": "openness_to_ai",
            "Scale / economic capacity": "economic_capacity",
        }.get(label)
        if dim_key and dim_key not in dq_by_dimension:
            dq_by_dimension[dim_key] = quote.strip()

    breakdown: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    positive: list[dict[str, str]] = []

    for key, label in dimensions:
        raw = qual.get(key)
        if key == "authority_role" and raw is None:
            raw = qual.get("authority")
        if key == "economic_capacity" and raw is None:
            raw = qual.get("ability_to_pay")
        display = _format_qual_dimension_value(key, raw)
        reason = reasons.get(key)
        if not isinstance(reason, str) or not reason.strip():
            reason = None
        else:
            reason = reason.strip()
        # Value-stage budget outcome can leave an assessment-time "not assessed" reason.
        if key == "budget_readiness":
            budget_norm = str(raw or "").strip().lower()
            reason_l = (reason or "").lower()
            if budget_norm == "ready" and reason and (
                "not assessed" in reason_l or "not been assessed" in reason_l
            ):
                reason = "Marked ready during value / price conditioning."
            elif budget_norm == "not_assessed":
                display = "Not assessed"
                if not reason:
                    reason = "Budget readiness is set later in value / price conditioning."
        tone = _qual_dimension_tone(key, raw)
        row = {
            "key": key,
            "label": label,
            "value": display,
            "raw": raw,
            "reason": reason,
            "tone": tone,
            "evidence_quote": dq_by_dimension.get(key),
        }
        breakdown.append(row)
        if _qual_dimension_unresolved(key, raw):
            unresolved.append({"label": label, "value": display, "reason": reason or ""})
        elif _qual_dimension_positive(key, raw):
            positive.append({"label": label, "value": display, "reason": reason or ""})

    disqualifiers: list[dict[str, str]] = []
    for item in qual.get("disqualifiers") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        quote = item.get("evidence_quote")
        if not (isinstance(code, str) and code.strip() and isinstance(quote, str) and quote.strip()):
            continue
        label, factor = humanize_disqualifier(code.strip(), item)
        disqualifiers.append(
            {
                "code": code.strip(),
                "label": label,
                "factor_value": factor,
                "evidence_quote": quote.strip(),
            }
        )

    explanation = qual.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = None
    else:
        explanation = explanation.strip()

    if status_norm == QualificationStatus.UNQUALIFIED.value:
        why_title = "Why unqualified"
        why_kind = "unqualified"
    elif status_norm == QualificationStatus.QUALIFIED.value:
        why_title = "Why assessment qualified"
        why_kind = "qualified"
    else:
        why_title = "Why pending / unclear"
        why_kind = "pending"

    return {
        "status": status_norm,
        "assessment_status": status_norm,
        "breakdown": breakdown,
        "unresolved": unresolved,
        "positive_signals": positive,
        "disqualifiers": disqualifiers,
        "explanation": explanation,
        "why_title": why_title,
        "why_kind": why_kind,
        # Never expose internal generation source in transparency payload.
        "calendar_eligible": is_calendar_eligible(
            qualification=qual,
            qualification_status=status_norm,
            # Transparency helper: never claim calendar from assessment alone.
            proof_demo_completed=False,
            value_price_conditioning_completed=False,
        ),
    }


def _format_qual_dimension_value(key: str, raw: Any) -> str:
    if raw is None or raw == "":
        return "unresolved"
    if key == "real_money_problem":
        if raw is True:
            return "yes"
        if raw is False:
            return "no"
        return "unresolved"
    text = str(raw).strip()
    return text if text else "unresolved"


def _qual_dimension_unresolved(key: str, raw: Any) -> bool:
    if key == "budget_readiness":
        # Expected during assessment; not a missing qualification signal.
        text = str(raw or "").strip().lower()
        return text in {"", "unclear", "unknown", "null", "none", "—", "-"}
    if raw is None or raw == "":
        return True
    if key == "real_money_problem":
        return not isinstance(raw, bool)
    text = str(raw).strip().lower()
    return text in {"unknown", "unclear", "null", "none", "—", "-"}


def _qual_dimension_positive(key: str, raw: Any) -> bool:
    if _qual_dimension_unresolved(key, raw):
        return False
    if key == "real_money_problem":
        return raw is True
    text = str(raw).strip().lower()
    positive = {
        "icp_fit": {"strong", "moderate"},
        "complexity": {"medium", "high"},
        "authority_role": {"decision_maker", "influencer"},
        "growth_change_readiness": {"high", "medium"},
        "openness_to_ai": {"high", "medium"},
        "economic_capacity": {"likely"},
        "budget_readiness": {"ready"},
        "ability_to_pay": {"likely"},
    }
    return text in positive.get(key, set())


def _qual_dimension_negative(key: str, raw: Any) -> bool:
    if _qual_dimension_unresolved(key, raw):
        return False
    if key == "real_money_problem":
        return raw is False
    text = str(raw).strip().lower()
    negative = {
        "icp_fit": {"weak"},
        "complexity": {"low"},
        "authority_role": {"not_decision_maker"},
        "growth_change_readiness": {"low"},
        "openness_to_ai": {"low"},
        "economic_capacity": {"unlikely"},
        "budget_readiness": {"not_ready"},
        "ability_to_pay": {"unlikely"},
    }
    return text in negative.get(key, set())


def _qual_dimension_tone(key: str, raw: Any) -> str:
    if _qual_dimension_unresolved(key, raw):
        return "warn"
    if _qual_dimension_negative(key, raw):
        return "bad"
    if _qual_dimension_positive(key, raw):
        return "ok"
    return "neutral"


class AssessmentCompletionService:
    def __init__(
        self,
        db: Session,
        *,
        openai_call: Any | None = None,
        event_service: AssessmentEventService | None = None,
    ) -> None:
        self.db = db
        self._openai_call = openai_call
        self.events = event_service or AssessmentEventService(db)

    def should_complete(
        self,
        profile: AssessmentProfile,
        *,
        turn_meta: dict[str, Any] | None = None,
        question_count: int = 0,
        force_hard_limit: bool = False,
        ai_sufficient: bool = False,
        assistant_ready_signal: bool = False,
        assistant_message: str | None = None,
    ) -> bool:
        """Complete only when every required assessment field is resolved."""
        del turn_meta, question_count, force_hard_limit, ai_sufficient
        del assistant_ready_signal, assistant_message
        return is_ready_for_completion(profile.structured_state)

    def complete(
        self,
        token: str,
        link: CampaignProspect,
        conversation: AssessmentConversation,
        profile: AssessmentProfile,
        *,
        generate_result_fn: Any,
        generate_qualification_fn: Any | None = None,
    ) -> AssessmentProfile:
        """Idempotent completion: result → qualification → status/stage updates."""
        if profile.result_payload is None:
            profile.result_payload = generate_result_fn(link, conversation, profile)
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)

        self.events.assessment_completed(token)
        self.db.refresh(link)
        link.journey_stage = JourneyStage.ASSESSMENT_COMPLETED
        link.last_activity_at = datetime.now(timezone.utc)
        self.db.add(link)
        self.db.commit()

        if profile.qualification_status == QualificationStatus.PENDING:
            qual_fn = generate_qualification_fn or self.qualify_from_state
            qualification = qual_fn(link, conversation, profile)
            # Assessment qualifier must never confirm budget or unlock calendar.
            if not isinstance(qualification, dict):
                qualification = empty_qualification()
            qualification["budget_readiness"] = "not_assessed"
            status_raw = str(
                qualification.get("assessment_status")
                or qualification.get("status")
                or QualificationStatus.PENDING.value
            ).lower()
            if status_raw not in _STATUS:
                status_raw = QualificationStatus.PENDING.value
            qualification["status"] = status_raw
            qualification["assessment_status"] = status_raw
            profile.qualification = qualification
            if status_raw == QualificationStatus.QUALIFIED.value:
                profile.qualification_status = QualificationStatus.QUALIFIED
                # Assessment-qualified = eligible for Result → Proof (not calendar).
                link.journey_stage = JourneyStage.QUALIFIED
            elif status_raw == QualificationStatus.UNQUALIFIED.value:
                profile.qualification_status = QualificationStatus.UNQUALIFIED
                link.journey_stage = JourneyStage.UNQUALIFIED_EXIT
            else:
                profile.qualification_status = QualificationStatus.PENDING
                link.journey_stage = JourneyStage.PENDING_REVIEW
                profile.qualification["status"] = QualificationStatus.PENDING.value
                profile.qualification["assessment_status"] = QualificationStatus.PENDING.value
            # Hard gate: assessment completion never unlocks Jeanne's calendar.
            if is_calendar_eligible(
                link=link,
                profile=profile,
            ):
                logger.error(
                    "Calendar eligibility true after assessment qualify — forcing budget_readiness=not_assessed"
                )
                profile.qualification["budget_readiness"] = "not_assessed"
            self.db.add(profile)
            self.db.add(link)
            self.db.commit()
            self.db.refresh(profile)
            self.db.refresh(link)
        self._copy_answers_to_prospect(link, profile)
        return profile

    def _copy_answers_to_prospect(self, link: CampaignProspect, profile: AssessmentProfile) -> None:
        """Headcount and revenue the prospect gave in the assessment replace the estimates on the prospect record."""
        try:
            prospect = getattr(link, "prospect", None)
            if prospect is None:
                return
            state = normalize_structured_state(profile.structured_state)
            changed = False
            for field, column in (("headcount", "headcount_band"), ("revenue_range", "revenue_band")):
                entry = state.get(field)
                if not isinstance(entry, dict) or entry.get("status") != "known" or entry.get("source") != "assessment":
                    continue
                value = entry.get("value")
                text = str(value).strip()[:100] if value not in (None, "") else ""
                if text and getattr(prospect, column) != text:
                    setattr(prospect, column, text)
                    changed = True
            if changed:
                self.db.add(prospect)
                self.db.commit()
        except Exception:  # noqa: BLE001 - copying must never break finishing an assessment
            logger.warning("Could not copy assessment answers to the prospect", exc_info=True)
            self.db.rollback()

    def qualify_from_state(
        self,
        link: CampaignProspect,
        conversation: AssessmentConversation,
        profile: AssessmentProfile,
    ) -> dict[str, Any]:
        """Single structured AI qualification call — no NL disqualify regex."""
        state = normalize_structured_state(profile.structured_state)
        pending = empty_qualification()
        pending["explanation"] = (
            "Insufficient qualification evidence — missing fields are not treated as negative."
        )

        user_messages = _user_message_texts(conversation)

        if self._openai_call is None:
            return pending
        try:
            history = "\n".join(
                f"{m.role.value}: {m.content}" for m in _ordered_messages(conversation)[-16:]
            )
            raw = self._openai_call(
                [
                    {"role": "system", "content": self._qualification_prompt()},
                    {
                        "role": "user",
                        "content": f"Structured state:\n{state}\n\nConversation:\n{history}",
                    },
                ]
            )
            parsed = parse_extractor_json(raw)
            return validate_qualification_payload(parsed, user_messages=user_messages)
        except Exception:  # noqa: BLE001
            logger.warning("Qualification LLM call failed; pending")
            return pending

    def requalify(
        self,
        link: CampaignProspect,
        conversation: AssessmentConversation,
        profile: AssessmentProfile,
        *,
        preserve_journey: bool = False,
    ) -> AssessmentProfile:
        """Force re-run qualification with the current prompt + assessment model."""
        from sqlalchemy.orm.attributes import flag_modified

        qualification = self.qualify_from_state(link, conversation, profile)
        if not isinstance(qualification, dict):
            qualification = empty_qualification()
        qualification["budget_readiness"] = "not_assessed"
        status_raw = str(
            qualification.get("assessment_status")
            or qualification.get("status")
            or QualificationStatus.PENDING.value
        ).lower()
        if status_raw not in _STATUS:
            status_raw = QualificationStatus.PENDING.value
        qualification["status"] = status_raw
        qualification["assessment_status"] = status_raw
        profile.qualification = qualification
        if status_raw == QualificationStatus.QUALIFIED.value:
            profile.qualification_status = QualificationStatus.QUALIFIED
            if not preserve_journey:
                link.journey_stage = JourneyStage.QUALIFIED
        elif status_raw == QualificationStatus.UNQUALIFIED.value:
            profile.qualification_status = QualificationStatus.UNQUALIFIED
            if not preserve_journey:
                link.journey_stage = JourneyStage.UNQUALIFIED_EXIT
        else:
            profile.qualification_status = QualificationStatus.PENDING
            if not preserve_journey:
                link.journey_stage = JourneyStage.PENDING_REVIEW
            profile.qualification["status"] = QualificationStatus.PENDING.value
            profile.qualification["assessment_status"] = QualificationStatus.PENDING.value
        flag_modified(profile, "qualification")
        self.db.add(profile)
        self.db.add(link)
        self.db.commit()
        self.db.refresh(profile)
        self.db.refresh(link)
        return profile

    def _qualification_prompt(self) -> str:
        row = self.db.get(AIReplySettings, 1) if self.db is not None else None
        prompt = getattr(row, "qualification_system_prompt", None) if row is not None else None
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
        return DEFAULT_QUALIFICATION_SYSTEM_PROMPT

    @staticmethod
    def coverage_ok(state: dict[str, Any] | None) -> bool:
        return backend_coverage_satisfied(state)


def _ordered_messages(conversation: AssessmentConversation) -> list[Any]:
    msgs = list(conversation.messages) if conversation.messages is not None else []
    return sorted(msgs, key=lambda m: (m.created_at, str(m.id)))


def _user_message_texts(conversation: AssessmentConversation) -> list[str]:
    from acquisition.models.assessment import AssessmentMessageRole

    return [
        m.content
        for m in _ordered_messages(conversation)
        if getattr(m.role, "value", m.role) == AssessmentMessageRole.USER.value
        or str(getattr(m.role, "value", m.role)) == "user"
    ]
