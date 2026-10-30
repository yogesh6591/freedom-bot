"""Generate Jeanne's internal pre-call brief from assessment evidence.

Booking confirmation always commits first. Brief failures never undo booking.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from acquisition.config import get_settings
from acquisition.models.assessment import (
    AssessmentConversation,
    AssessmentMessageRole,
    AssessmentProfile,
)
from acquisition.models.campaign import CampaignProspect
from acquisition.services.assessment_extraction import (
    concrete_value,
    field_display,
    normalize_structured_state,
    parse_extractor_json,
)
from acquisition.services.booking_service import BOOKING_STATUS_CONFIRMED

logger = logging.getLogger(__name__)

PRE_CALL_BRIEF_MODEL = "gpt-5.6-luna"
PRE_CALL_BRIEF_REASONING = {"effort": "medium"}
BRIEF_STATUS_READY = "ready"
BRIEF_STATUS_FAILED = "failed"

NO_FINANCIAL_ESTIMATE = (
    "No supportable financial-impact estimate was established during the assessment."
)
NO_CONCERNS = "No major concerns were captured during the assessment."
NO_QUESTIONS = "No specific prospect questions were captured yet."
NO_CRITICAL_GAPS = (
    "No critical information gaps were identified from the completed assessment."
)
NO_OPPORTUNITY = (
    "No specific FreedomBot opportunity was established beyond the assessment findings."
)

BRIEF_SECTION_KEYS = (
    "business_snapshot",
    "possible_inefficiencies",
    "key_numbers",
    "financial_impact",
    "freedombot_opportunity",
    "concerns",
    "missing_information",
    "prospect_questions",
    "likely_fit",
    "discussion_points",
)

# Strict JSON schema for Responses API text.format (openai>=1.x / 2.x).
PRE_CALL_BRIEF_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "business_snapshot": {"type": "string"},
        "possible_inefficiencies": {
            "type": "array",
            "items": {"type": "string"},
        },
        "key_numbers": {
            "type": "array",
            "items": {"type": "string"},
        },
        "financial_impact": {"type": "string"},
        "freedombot_opportunity": {
            "type": "array",
            "items": {"type": "string"},
        },
        "concerns": {
            "type": "array",
            "items": {"type": "string"},
        },
        "missing_information": {
            "type": "array",
            "items": {"type": "string"},
        },
        "prospect_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "likely_fit": {"type": "string"},
        "discussion_points": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": list(BRIEF_SECTION_KEYS),
}

PRE_CALL_BRIEF_INSTRUCTIONS = """
You create an INTERNAL pre-call brief for Jeanne before a FreedomBot consultation.

The brief is generated from a prospect's completed assessment and existing qualification evidence.

Your job is NOT to perform a new assessment.

Your job is to turn the supplied evidence into a concise, accurate one-page preparation brief so Jeanne does not have to reread the full assessment conversation before the meeting.

Use ONLY the evidence provided to you.

==================================================
PRIMARY PURPOSE
==================================================

The brief must help Jeanne understand, quickly:

- who the company/prospect is
- what operational problems may exist
- what measurable numbers matter
- whether any financial-impact estimate was actually established
- what concerns or reservations the prospect expressed
- what important information is still unresolved
- why FreedomBot may or may not be a fit
- what questions the prospect actually asked

The brief should feel like executive meeting preparation.

It must NOT feel like:
- a sales proposal
- an implementation plan
- a technical architecture
- a transcript summary
- a CRM data dump

==================================================
OUTPUT
==================================================

Return ONLY valid JSON with exactly these keys:

{
  "business_snapshot": "",
  "possible_inefficiencies": [],
  "key_numbers": [],
  "financial_impact": "",
  "freedombot_opportunity": [],
  "concerns": [],
  "missing_information": [],
  "prospect_questions": [],
  "likely_fit": "",
  "discussion_points": []
}

Do not return markdown.
Do not return commentary outside the JSON.
Do not add additional keys.

==================================================
1. BUSINESS SNAPSHOT
==================================================

Write 2–3 concise sentences maximum.

Summarize only business context that is explicitly supported by the evidence.

May include:
- relevant business goal
- functions involved
- important workflows
- tools/systems
- operational dependency
- major business context

DO NOT infer:
- industry
- services offered
- customer type
- market position
- growth rate
- company strategy

unless explicitly present in evidence.

Do not repeat revenue/headcount here unless they materially help explain the situation; those appear elsewhere in the brief.

Do not use marketing language.

==================================================
2. POSSIBLE INEFFICIENCIES
==================================================

Return 2–4 concise bullets where evidence supports them.

Use cautious language.

Examples of acceptable language:

"Sales-to-delivery handoffs appear inconsistent."

"Assessment indicates important pricing guidance is not documented centrally."

"Routine project decisions appear dependent on leadership."

Do NOT convert hypotheses into proven facts.

Do NOT invent root causes.

Do NOT produce generic FreedomBot problems merely because they are common in businesses.

If evidence is weak, say that clearly rather than filling the section with assumptions.

==================================================
3. KEY NUMBERS
==================================================

Return only meaningful numbers already present in evidence.

Examples:

"$10–25M revenue range"

"50–100 employees"

"Approximately two-day delay"

"Approximately 10–15 hours of waiting/rework"

Do not calculate new numbers.

Do not estimate values not already present.

Maximum 4 important numbers unless there is a strong reason otherwise.

==================================================
4. FINANCIAL IMPACT
==================================================

This section has a strict rule.

Only describe a dollar financial-impact estimate when:

evidence.has_financial_estimate == true

AND

evidence.financial_estimate_text contains a supported estimate.

If both are present:
paraphrase the stored estimate cautiously.

Use language such as:

"Directional estimate..."
"Approximately..."
"Based on the assumptions captured during the assessment..."

Never call it guaranteed ROI or guaranteed savings.

If a supported financial estimate was NOT established, return EXACTLY:

"No supportable financial-impact estimate was established during the assessment."

Do not create a new financial calculation.

==================================================
5. FREEDOMBOT OPPORTUNITY
==================================================

Return up to 3 concise high-level opportunity areas.

These must be supported by:

- existing opportunity areas
- assessment findings
- personalized value evidence

Examples:

"Improve consistency in sales-to-delivery handoffs."

"Centralize pricing and quality guidance."

"Reduce routine escalation to leadership while preserving human review."

Do NOT generate:

- technical architecture
- database design
- detailed integrations
- workflow diagrams
- implementation phases
- tool recommendations
- detailed automation blueprint

IMPORTANT:

If evidence does not establish a specific opportunity, DO NOT insert generic FreedomBot capabilities just to populate the section.

It is acceptable to state:

"No specific FreedomBot opportunity was established beyond the assessment findings."

==================================================
6. CONCERNS / RISKS
==================================================

Only include concerns the PROSPECT actually expressed or that are explicitly represented as concerns/objections in the evidence.

Examples of real concerns:

- hesitation about AI framed as worry or reluctance
- budget uncertainty / inability to proceed
- adoption/change resistance framed as a blocker
- privacy/security concern
- uncertainty about implementation that may prevent progress

IMPORTANT:

Do not source concerns from readiness_fields.ai_readiness,
qualification.openness_to_ai, or a human-review preference unless
the prospect explicitly framed that statement as a concern,
hesitation, objection, blocker, or risk.

Do not treat any of these as concerns by themselves:

- ai_readiness
- openness_to_ai
- change_readiness
- desire for human review
- decision-maker approval requirement
- implementation ownership

Those may inform likely_fit, readiness context, or discussion_points —
but NOT Concerns / Risks.

AI openness/readiness is NOT automatically a concern.

A preference for human review is NOT automatically a concern.

Only classify something as a concern when the prospect expressed it as:
- hesitation
- objection
- limitation
- risk
- condition
- uncertainty
- worry
- blocker
- material condition that may prevent progress

Do not classify a normal operating preference as a concern.

If no real concern was captured, return exactly:

["No major concerns were captured during the assessment."]

Do not invent objections.

==================================================
7. MISSING INFORMATION
==================================================

Return only material information that is genuinely unresolved from the assessment and relevant to Jeanne's consultation.

Primary basis should be:

- material_missing_field_keys
- explicit unknown / declined / unresolved assessment states
- contradictions or uncertainty already visible in the evidence

Do NOT create missing-information items merely because they could be useful to know.

Do NOT turn this into a generic discovery checklist.

For example:

Good:
"Exact time/cost impact was not established."

Bad:
"Ask what success looks like."

unless success criteria were actually an identified unresolved field or issue.

Maximum 4 items.

If no material gap exists, return:

["No critical information gaps were identified from the completed assessment."]

==================================================
8. PROSPECT QUESTIONS
==================================================

Include only questions the prospect actually asked.

Use:
- user_messages
- user_questions

Prefer near-verbatim wording.

A question must genuinely be something the prospect asked or requested.

Do NOT turn normal statements into questions.

Do NOT invent likely questions.

Do NOT create questions Jeanne should ask here.

Those belong only in discussion_points.

Maximum 4.

If none were captured, return exactly:

["No specific prospect questions were captured yet."]

==================================================
9. LIKELY FREEDOMBOT FIT
==================================================

Write 1–2 concise sentences.

Use ONLY existing qualification evidence such as:

- assessment_status
- icp_fit
- complexity
- real_money_problem
- economic_capacity
- authority
- growth/change readiness
- openness to AI
- budget readiness

Explain the evidence behind likely fit.

Example style:

"ABC Consulting appears to be a strong potential fit based on measurable handoff friction, decision-maker participation, operational complexity, and openness to AI-supported process improvement."

Only use words such as "strong" when existing qualification evidence supports that interpretation.

Do NOT create:
- numeric score
- percentage
- new qualification category
- probability of purchase
- sales forecast

Do not override the existing qualification result.

==================================================
10. DISCUSSION POINTS
==================================================

This section is an INTERNAL enhancement for Jeanne.

Generate maximum 3 concise questions Jeanne could ask during the consultation.

These questions must be derived from:

- material missing information
- highest-impact assessment findings
- actual concerns
- actual prospect questions

Do not invent new business facts inside the question.

Good example:

"Which part of the sales-to-delivery handoff creates the most downstream rework?"

Bad example:

"Would replacing your current CRM save your team 30 hours per week?"

unless that evidence actually exists.

If there are fewer than 3 meaningful grounded questions, return fewer than 3.

DO NOT pad the list with generic questions merely to reach a number.

==================================================
GROUNDING RULES — STRICT
==================================================

Use ONLY the supplied assessment evidence.

Never invent:

- company facts
- industry
- customer type
- company services
- revenue
- headcount
- tools
- workflows
- operational problems
- prospect concerns
- questions
- costs
- financial impact
- ROI
- savings
- implementation requirements

Known facts must stay facts.

Possible inefficiencies must remain possibilities.

Unknown information must remain unknown.

Do not silently convert absence of evidence into evidence of absence.

Do not overstate certainty.

==================================================
STYLE
==================================================

This is an executive one-page internal brief.

Be:

- concise
- business-focused
- neutral
- grounded
- easy to scan

Avoid:

- promotional language
- repetition
- excessive explanation
- AI jargon
- engineering terminology
- verbose paragraphs
""".strip()


def _known_field(state: dict[str, Any], key: str) -> Any:
    return concrete_value(state.get(key))


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "; ".join(parts) if parts else None
    text = str(value).strip()
    return text or None


def _user_messages(conversation: AssessmentConversation | None) -> list[str]:
    if conversation is None:
        return []
    out: list[str] = []
    for msg in conversation.messages or []:
        if msg.role == AssessmentMessageRole.USER and (msg.content or "").strip():
            out.append(msg.content.strip())
    return out


def _likely_questions(messages: list[str]) -> list[str]:
    questions: list[str] = []
    for msg in messages:
        compact = " ".join(msg.split())
        if "?" in compact:
            # Prefer the sentence that contains the question mark.
            for part in compact.replace("?", "?|").split("|"):
                chunk = part.strip()
                if chunk.endswith("?") and len(chunk) > 8:
                    questions.append(chunk)
        elif compact.lower().startswith(("can ", "how ", "what ", "will ", "would ", "do you")):
            if len(compact) > 12:
                questions.append(compact if compact.endswith("?") else f"{compact}?")
    # De-dupe preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for q in questions:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:12]


def _opportunity_labels(result: dict[str, Any]) -> list[str]:
    areas = result.get("opportunity_areas") or []
    labels: list[str] = []
    if isinstance(areas, list):
        for item in areas:
            if isinstance(item, dict):
                label = str(item.get("label") or "").strip()
                if label:
                    labels.append(label)
            elif isinstance(item, str) and item.strip():
                labels.append(item.strip())
    return labels


def build_pre_call_evidence(
    *,
    profile: AssessmentProfile,
    link: CampaignProspect,
    conversation: AssessmentConversation | None,
) -> dict[str, Any]:
    """Structured evidence bundle for AI synthesis. No invented fields."""
    prospect = link.prospect
    state = normalize_structured_state(profile.structured_state)
    result = (
        profile.result_payload if isinstance(profile.result_payload, dict) else {}
    ) or {}
    qual = dict(profile.qualification) if isinstance(profile.qualification, dict) else {}
    # Never feed internal generation source into the brief model.
    qual.pop("qualification_source", None)
    user_msgs = _user_messages(conversation)

    revenue = _as_text(_known_field(state, "revenue_range")) or (
        (prospect.revenue_band or "").strip() or None if prospect else None
    )
    headcount = _as_text(_known_field(state, "headcount")) or (
        (prospect.headcount_band or "").strip() or None if prospect else None
    )
    title = _as_text(_known_field(state, "authority")) or (
        (prospect.executive_title or "").strip() or None if prospect else None
    )

    impact_kind = result.get("impact_kind")
    observed = result.get("observed_impact") or result.get("directional_impact")
    observed_text = _as_text(observed)
    has_financial = (
        impact_kind == "directional_financial" and bool(observed_text)
    )

    # Material missing fields only (ops / impact / readiness), not every empty key.
    material_keys = (
        "workflows",
        "knowledge_locations",
        "time_cost_impact",
        "bottlenecks",
        "pain_points",
        "authority",
        "change_readiness",
        "ai_readiness",
    )
    missing_material: list[str] = []
    for key in material_keys:
        display = field_display(state.get(key))
        if display["status"] == "missing":
            missing_material.append(key)

    return {
        "prospect": {
            "name": (prospect.executive_name if prospect else None),
            "company": (prospect.company if prospect else None),
            "title": title,
            "email": (prospect.email if prospect else None),
        },
        "company_context": {
            "revenue": revenue,
            "headcount": headcount,
            "functions": _as_text(_known_field(state, "functions")),
            "tools": _as_text(_known_field(state, "tools")),
            "workflows": _as_text(_known_field(state, "workflows")),
            "knowledge_locations": _as_text(_known_field(state, "knowledge_locations")),
            "goals": _as_text(_known_field(state, "goals")),
        },
        "assessment": {
            "pain_points": _as_text(_known_field(state, "pain_points")),
            "bottlenecks": _as_text(_known_field(state, "bottlenecks")),
            "time_cost_impact": _as_text(_known_field(state, "time_cost_impact")),
            "inefficiencies_summary": result.get("inefficiencies_summary"),
            "observed_impact": observed_text,
            "impact_kind": impact_kind,
            "opportunity_areas": _opportunity_labels(result),
            "personalized_value_text": (profile.personalized_value_text or "").strip() or None,
        },
        "financial": {
            "has_financial_estimate": has_financial,
            "financial_estimate_text": observed_text if has_financial else None,
        },
        "has_financial_estimate": has_financial,
        "financial_estimate_text": observed_text if has_financial else None,
        "qualification": {
            "assessment_status": qual.get("assessment_status") or qual.get("status"),
            # Compatibility: some records store ability_to_pay instead of economic_capacity.
            "economic_capacity": (
                qual.get("economic_capacity") or qual.get("ability_to_pay")
            ),
            "budget_readiness": qual.get("budget_readiness"),
            "authority": qual.get("authority"),
            "authority_role": qual.get("authority_role"),
            "growth_change_readiness": qual.get("growth_change_readiness"),
            "openness_to_ai": qual.get("openness_to_ai"),
            "real_money_problem": qual.get("real_money_problem"),
            "icp_fit": qual.get("icp_fit"),
            "complexity": qual.get("complexity"),
            "explanation": qual.get("explanation"),
            "notes": qual.get("notes"),
            "disqualifiers": qual.get("disqualifiers") or [],
        },
        "readiness_fields": {
            "change_readiness": _as_text(_known_field(state, "change_readiness")),
            "ai_readiness": _as_text(_known_field(state, "ai_readiness")),
            "authority": _as_text(_known_field(state, "authority")),
        },
        "booking": {
            "booking_status": link.booking_status,
            "meeting_start": link.meeting_start.isoformat() if link.meeting_start else None,
            "meeting_end": link.meeting_end.isoformat() if link.meeting_end else None,
            "meeting_timezone": link.meeting_timezone,
            "meeting_location": link.meeting_location,
        },
        "user_messages": user_msgs[-20:],
        "user_questions": _likely_questions(user_msgs),
        "material_missing_field_keys": missing_material,
    }


def _normalize_string_list(value: Any, *, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return items
    return list(fallback or [])


def _validate_brief(raw: dict[str, Any], *, evidence: dict[str, Any]) -> dict[str, Any]:
    snapshot = str(raw.get("business_snapshot") or "").strip()
    inefficiencies = _normalize_string_list(raw.get("possible_inefficiencies"))
    key_numbers = _normalize_string_list(raw.get("key_numbers"))
    financial = str(raw.get("financial_impact") or "").strip()
    opportunity = _normalize_string_list(raw.get("freedombot_opportunity"))
    concerns = _normalize_string_list(raw.get("concerns"), fallback=[NO_CONCERNS])
    missing = _normalize_string_list(raw.get("missing_information"), fallback=[NO_CRITICAL_GAPS])
    questions = _normalize_string_list(raw.get("prospect_questions"), fallback=[NO_QUESTIONS])
    likely_fit = str(raw.get("likely_fit") or "").strip()
    discussion = _normalize_string_list(raw.get("discussion_points"))

    # Hard constraint: unsupported financial impact cannot pass.
    if not evidence.get("has_financial_estimate"):
        financial = NO_FINANCIAL_ESTIMATE
    elif not financial:
        financial = evidence.get("financial_estimate_text") or NO_FINANCIAL_ESTIMATE

    # Soft length caps aligned with prompt limits.
    if len(snapshot) > 1200:
        snapshot = snapshot[:1197].rsplit(" ", 1)[0] + "…"
    inefficiencies = inefficiencies[:4]
    key_numbers = key_numbers[:4]
    opportunity = opportunity[:3] or [NO_OPPORTUNITY]
    concerns = concerns[:6] or [NO_CONCERNS]
    missing = missing[:4] or [NO_CRITICAL_GAPS]
    questions = questions[:4] or [NO_QUESTIONS]
    discussion = discussion[:3]
    if len(likely_fit) > 600:
        likely_fit = likely_fit[:597].rsplit(" ", 1)[0] + "…"

    if not snapshot:
        raise ValueError("missing_business_snapshot")
    if not inefficiencies:
        raise ValueError("missing_inefficiencies")
    if not likely_fit:
        raise ValueError("missing_likely_fit")
    # discussion_points may be empty when no grounded questions exist.

    # Persist only allowed keys.
    return {
        "business_snapshot": snapshot,
        "possible_inefficiencies": inefficiencies,
        "key_numbers": key_numbers,
        "financial_impact": financial,
        "freedombot_opportunity": opportunity,
        "concerns": concerns,
        "missing_information": missing,
        "prospect_questions": questions,
        "likely_fit": likely_fit,
        "discussion_points": discussion,
    }


def grounded_pre_call_brief_fallback(evidence: dict[str, Any]) -> dict[str, Any]:
    """Deterministic brief from stored evidence when AI is unavailable.

    Follows the same business rules as PRE_CALL_BRIEF_INSTRUCTIONS.
    """
    company = (evidence.get("prospect") or {}).get("company") or "the company"
    assessment = evidence.get("assessment") or {}
    company_ctx = evidence.get("company_context") or {}
    qual = evidence.get("qualification") or {}
    readiness = evidence.get("readiness_fields") or {}

    parts: list[str] = []
    if company_ctx.get("functions") or company_ctx.get("tools") or company_ctx.get("workflows"):
        bits = []
        if company_ctx.get("functions"):
            bits.append(f"functions around {company_ctx['functions']}")
        if company_ctx.get("tools"):
            bits.append(f"systems including {company_ctx['tools']}")
        if company_ctx.get("workflows"):
            bits.append(f"workflows involving {company_ctx['workflows']}")
        parts.append(f"Assessment context for {company} covers {'; '.join(bits)}.")
    else:
        parts.append(f"Assessment captured operational context for {company}.")
    if company_ctx.get("knowledge_locations"):
        parts.append(
            f"Company knowledge appears to live in {company_ctx['knowledge_locations']}."
        )
    if assessment.get("inefficiencies_summary"):
        parts.append(str(assessment["inefficiencies_summary"]).strip())
    snapshot = " ".join(parts)
    # Keep snapshot to ~2–3 sentences: take first three sentence chunks if long.
    sentences = [s.strip() for s in snapshot.replace("? ", "?. ").split(". ") if s.strip()]
    if len(sentences) > 3:
        snapshot = ". ".join(sentences[:3]).rstrip(".") + "."

    inefficiencies: list[str] = []
    summary = _as_text(assessment.get("inefficiencies_summary"))
    if summary:
        inefficiencies.append(summary)
    for key in ("pain_points", "bottlenecks"):
        val = _as_text(assessment.get(key))
        if val:
            line = f"Assessment indicates: {val}"
            if line not in inefficiencies and val not in inefficiencies:
                inefficiencies.append(line)
    if not inefficiencies:
        inefficiencies.append(
            "Possible inefficiencies were not clearly established in the assessment."
        )
    inefficiencies = inefficiencies[:4]

    key_numbers: list[str] = []
    for candidate in (
        company_ctx.get("revenue"),
        company_ctx.get("headcount"),
        assessment.get("time_cost_impact"),
        assessment.get("observed_impact") if not evidence.get("has_financial_estimate") else None,
    ):
        text = _as_text(candidate)
        if text and text not in key_numbers:
            key_numbers.append(text)
    key_numbers = key_numbers[:4]

    financial = (
        evidence.get("financial_estimate_text")
        if evidence.get("has_financial_estimate")
        else NO_FINANCIAL_ESTIMATE
    ) or NO_FINANCIAL_ESTIMATE

    opportunity = list(assessment.get("opportunity_areas") or [])[:3]
    if not opportunity and assessment.get("personalized_value_text"):
        opportunity = [str(assessment["personalized_value_text"]).strip()]
    if not opportunity:
        opportunity = [NO_OPPORTUNITY]

    # Only actual prospect questions — never synthesize from statements.
    questions = [q for q in (evidence.get("user_questions") or []) if str(q).strip()]
    if not questions:
        questions = [NO_QUESTIONS]
    questions = questions[:4]

    # AI readiness / human review preference is NOT automatically a concern.
    # Fallback has no structured objections channel → do not invent concerns.
    concerns = [NO_CONCERNS]

    missing_keys = evidence.get("material_missing_field_keys") or []
    missing_labels = {
        "workflows": "Specific workflow ownership was not fully established",
        "knowledge_locations": "Where operational knowledge lives was not fully established",
        "time_cost_impact": "Exact time/cost impact was not established",
        "bottlenecks": "Primary bottleneck ownership was not fully established",
        "pain_points": "Full pain-point inventory was not established",
        "authority": "Authority / decision-maker details were not fully established",
        "change_readiness": "Change readiness was not fully established",
        "ai_readiness": "AI readiness expectations were not fully established",
    }
    missing = [missing_labels[k] for k in missing_keys if k in missing_labels][:4]
    if not missing:
        missing = [NO_CRITICAL_GAPS]

    fit_bits: list[str] = []
    if qual.get("assessment_status") or qual.get("status"):
        fit_bits.append(f"assessment status {qual.get('assessment_status') or qual.get('status')}")
    if qual.get("icp_fit"):
        fit_bits.append(f"ICP fit {qual['icp_fit']}")
    if qual.get("complexity"):
        fit_bits.append(f"complexity {qual['complexity']}")
    if qual.get("real_money_problem"):
        fit_bits.append(f"real money problem {qual['real_money_problem']}")
    if qual.get("economic_capacity"):
        fit_bits.append(f"economic capacity {qual['economic_capacity']}")
    if readiness.get("authority") or qual.get("authority"):
        fit_bits.append("decision-maker / authority participation indicated")
    if readiness.get("ai_readiness") or qual.get("openness_to_ai"):
        fit_bits.append("openness to AI noted in assessment")
    if readiness.get("change_readiness") or qual.get("growth_change_readiness"):
        fit_bits.append("change readiness noted")
    if qual.get("budget_readiness"):
        fit_bits.append(f"budget readiness {qual['budget_readiness']}")
    if assessment.get("time_cost_impact") or assessment.get("observed_impact"):
        fit_bits.append("measurable operational friction captured")
    if qual.get("explanation"):
        fit_bits.append(str(qual["explanation"]).strip())
    likely_fit = (
        "Potential fit based on " + "; ".join(fit_bits[:5]) + "."
        if fit_bits
        else "Fit is not yet strongly established from qualification evidence."
    )

    # Discussion points: only when grounded in actual evidence. No padding.
    discussion: list[str] = []
    summary_text = _as_text(assessment.get("inefficiencies_summary"))
    pain = _as_text(assessment.get("pain_points"))
    workflows = _as_text(company_ctx.get("workflows"))
    if summary_text or pain or workflows:
        focus = workflows or pain or "the friction noted in the assessment"
        discussion.append(
            f"Which part of {focus} creates the most downstream impact today?"
        )
    if "time_cost_impact" in missing_keys:
        discussion.append(
            "What is the real time or cost impact of the friction described in the assessment?"
        )
    if questions and questions != [NO_QUESTIONS]:
        discussion.append(
            f"Follow up on the prospect's question: {questions[0]}"
        )
    discussion = discussion[:3]

    return _validate_brief(
        {
            "business_snapshot": snapshot,
            "possible_inefficiencies": inefficiencies,
            "key_numbers": key_numbers,
            "financial_impact": financial,
            "freedombot_opportunity": opportunity,
            "concerns": concerns,
            "missing_information": missing,
            "prospect_questions": questions,
            "likely_fit": likely_fit,
            "discussion_points": discussion,
        },
        evidence=evidence,
    )


def generate_pre_call_brief(
    *,
    evidence: dict[str, Any],
    openai_call: Callable[[list[dict[str, str]]], str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (brief_data, meta). Prefer OpenAI; fall back to grounded composition."""
    meta: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "fallback",
        "model": None,
    }
    if openai_call is not None:
        try:
            raw = openai_call(
                [
                    {"role": "system", "content": PRE_CALL_BRIEF_INSTRUCTIONS},
                    {
                        "role": "user",
                        "content": (
                            "Assessment evidence (JSON):\n"
                            f"{json.dumps(evidence, default=str)}"
                        ),
                    },
                ]
            )
            parsed = parse_extractor_json(raw)
            if not isinstance(parsed, dict):
                raise ValueError("brief_not_object")
            brief = _validate_brief(parsed, evidence=evidence)
            meta["source"] = "openai"
            meta["model"] = PRE_CALL_BRIEF_MODEL
            return brief, meta
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pre-call brief OpenAI failed: %s", type(exc).__name__)
            meta["openai_error"] = type(exc).__name__

    brief = grounded_pre_call_brief_fallback(evidence)
    return brief, meta


class PreCallBriefService:
    def __init__(
        self,
        db: Session,
        *,
        openai_call: Callable[[list[dict[str, str]]], str] | None = None,
    ) -> None:
        self.db = db
        self._openai_call = openai_call

    def is_booking_confirmed(self, link: CampaignProspect) -> bool:
        return (
            str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
            and link.booked_at is not None
        )

    def get_profile(self, link: CampaignProspect) -> AssessmentProfile | None:
        return self.db.scalar(
            select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id)
        )

    def get_conversation(self, link: CampaignProspect) -> AssessmentConversation | None:
        return self.db.scalar(
            select(AssessmentConversation)
            .where(AssessmentConversation.campaign_prospect_id == link.id)
            .options(selectinload(AssessmentConversation.messages))
        )

    def has_ready_brief(self, profile: AssessmentProfile | None) -> bool:
        if profile is None:
            return False
        return (
            (profile.pre_call_brief_status or "") == BRIEF_STATUS_READY
            and isinstance(profile.pre_call_brief_data, dict)
            and bool(profile.pre_call_brief_data)
        )

    def generate_after_booking_if_needed(self, link: CampaignProspect) -> dict[str, Any]:
        """Post-commit hook: generate once if missing. Never raises to callers."""
        try:
            if not self.is_booking_confirmed(link):
                return {"status": "skipped", "reason": "not_booked"}
            profile = self.get_profile(link)
            if profile is None:
                return {"status": "skipped", "reason": "no_profile"}
            if self.has_ready_brief(profile):
                return {"status": "skipped", "reason": "already_ready"}
            return self.generate_for_link(link, force=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Pre-call brief post-booking generation failed for link=%s: %s",
                link.id,
                type(exc).__name__,
            )
            try:
                profile = self.get_profile(link)
                if profile is not None:
                    profile.pre_call_brief_status = BRIEF_STATUS_FAILED
                    profile.pre_call_brief_error = f"{type(exc).__name__}: {exc}"[:2000]
                    self.db.add(profile)
                    self.db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to persist pre-call brief error state")
                self.db.rollback()
            return {"status": "failed", "error": type(exc).__name__}

    def generate_for_link(
        self,
        link: CampaignProspect,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Generate or regenerate. Persists success/failure on the same assessment profile."""
        if not self.is_booking_confirmed(link):
            return {"status": "skipped", "reason": "not_booked"}

        profile = self.get_profile(link)
        if profile is None:
            return {"status": "failed", "error": "no_profile"}

        if self.has_ready_brief(profile) and not force:
            return {
                "status": "ready",
                "brief": profile.pre_call_brief_data,
                "skipped": True,
            }

        conversation = self.get_conversation(link)
        # Ensure prospect is loaded for evidence.
        if link.prospect is None:
            self.db.refresh(link, attribute_names=["prospect"])

        evidence = build_pre_call_evidence(
            profile=profile, link=link, conversation=conversation
        )
        openai_call = self._openai_call or self._default_openai_call

        try:
            brief, meta = generate_pre_call_brief(
                evidence=evidence,
                openai_call=openai_call,
            )
            profile.pre_call_brief_data = brief
            flag_modified(profile, "pre_call_brief_data")
            profile.pre_call_brief_generated_at = datetime.now(timezone.utc)
            profile.pre_call_brief_status = BRIEF_STATUS_READY
            profile.pre_call_brief_error = None
            profile.pre_call_brief_model = meta.get("model") or (
                PRE_CALL_BRIEF_MODEL if meta.get("source") == "openai" else "fallback"
            )
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)
            return {
                "status": "ready",
                "brief": brief,
                "meta": meta,
                "source": meta.get("source"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Pre-call brief generation failed for link=%s: %s",
                link.id,
                type(exc).__name__,
            )
            profile.pre_call_brief_status = BRIEF_STATUS_FAILED
            profile.pre_call_brief_error = f"{type(exc).__name__}: {exc}"[:2000]
            profile.pre_call_brief_generated_at = datetime.now(timezone.utc)
            self.db.add(profile)
            self.db.commit()
            return {"status": "failed", "error": str(exc)}

    def ensure_for_admin(self, link: CampaignProspect) -> dict[str, Any]:
        """Lazy generation when Admin opens a booked prospect without a ready brief."""
        return self.generate_for_link(link, force=False)

    def regenerate(self, link: CampaignProspect) -> dict[str, Any]:
        return self.generate_for_link(link, force=True)

    def _default_openai_call(self, input_messages: list[dict[str, str]]) -> str:
        settings = get_settings()
        api_key = (settings.openai_api_key or "").strip()
        if not api_key:
            raise RuntimeError("no_openai")
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        # SDK (openai 2.x) supports Responses text.format json_schema for strict JSON.
        response = client.responses.create(
            model=PRE_CALL_BRIEF_MODEL,
            reasoning=PRE_CALL_BRIEF_REASONING,
            input=input_messages,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pre_call_brief",
                    "strict": True,
                    "schema": PRE_CALL_BRIEF_JSON_SCHEMA,
                }
            },
        )
        text = (getattr(response, "output_text", None) or "").strip()
        if not text:
            chunks: list[str] = []
            for item in getattr(response, "output", None) or []:
                for part in getattr(item, "content", None) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        chunks.append(str(part_text))
            text = "\n".join(chunks).strip()
        if not text:
            raise RuntimeError("empty_openai_response")
        return text
