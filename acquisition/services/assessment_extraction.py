"""Structured field merge, completion coverage, and AI ready-signal helpers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from acquisition.models.journey import (
    STRUCTURED_FIELD_KEYS,
    FieldStatus,
    empty_field,
    empty_structured_state,
)

logger = logging.getLogger(__name__)

ASSESSMENT_READY_MARKER = "ASSESSMENT_READY"

# Provenance for structured field values (assessment snapshot precedence).
FIELD_SOURCE_PROSPECT_SEED = "prospect_seed"
FIELD_SOURCE_ASSESSMENT = "assessment"

EXTRACTOR_INSTRUCTIONS = """
You extract structured business-assessment fields from a conversation.
Return ONLY a JSON object.

Field semantics (use strictly — YOU decide status from meaning, not from keyword lists):
- known: user provided usable information
- unknown: user explicitly communicates they do not know / cannot determine it
- declined: user explicitly chooses not to provide it
- not_applicable: user clearly indicates it does not apply
- missing: you learned nothing reliable about this field on this turn (omit the field)

For EVERY assessment field you update (including headcount), return a strict object:
  "field_name": {
    "status": "known|unknown|declined|not_applicable|missing",
    "value": <concrete value or null>,
    "evidence_quote": "<exact quote from a USER message>"
  }

READ THE WHOLE CONVERSATION EVERY TURN:
- Every turn, check every field that is still missing in the existing state against ALL user messages so far,
  not only the latest one. If any earlier or the latest user message already answers a missing field, fill it now.
- A field is known when the user's own words state it or directly imply it (for example, describing themselves
  as the founder or owner states that they can decide; "about thirty of us" states headcount). Quote the words
  that support it. Do not go beyond what their words support.
- One rich answer commonly fills many fields: fill every field the conversation supports.

Rules for all fields:
- known requires a non-empty value AND evidence_quote copied from a USER message in the conversation.
- unknown|declined|not_applicable require value=null AND evidence_quote from a USER message in the conversation.
- status=missing or omit the field: no update.
- Do NOT set source / evidence_message_id / provenance (backend owns those).
- evidence_quote must be an exact substring of one USER message in the conversation (assistant text is never evidence).
  Prefer the most recent user message that supports the field.
- One user message may update multiple fields; each field needs its own evidence_quote
  (quotes may reuse the same sentence when it genuinely supports multiple fields).
- Normalize values semantically when helpful (e.g. tools list, "ten to fifteen hours" → "10–15 hours per week").
- Do not invent values. Do not guess. Do not turn assistant inferences into user facts.

HEADCOUNT (same evidence object, plus scope):
  "headcount": {
    "status": "known|unknown|declined|not_applicable|missing",
    "value": <integer or null>,
    "evidence_quote": "<exact quote from a USER message>",
    "scope": "company|team|unknown"
  }
Additional headcount rules:
- status=known requires integer value, evidence_quote, and scope.
- Prefer company-wide headcount when both team and company are stated.
- scope=company when the user is clearly describing overall organization/business size,
  even if they do not literally say "company" (e.g. "We have 25 employees.",
  "There are twenty-five of us." as overall business size, "We're about two dozen people.",
  "Our firm has 30 people.").
- scope=team when explicitly describing a subset (e.g. "Our implementation team has 25 people.",
  "Sales has 12 people.").
- scope=unknown ONLY when it is genuinely unclear whether the count is company-wide or a subset.
- Backend stores primary company headcount only when scope=company; team/unknown are not stored
  as company headcount. Do not invent company-wide numbers from team-only statements.
- Semantic understanding is required (e.g. "twenty-five", "two dozen" ≈ 24).

CONVERSATION_FOCUS (required each turn when an assistant question is being answered):
  "conversation_focus": {
    "focus_label": "<short semantic label for the business issue of the assistant question the latest user message answers>",
    "same_business_issue_as_previous": true|false,
    "question_stage": "business_context|problem_discovery|root_cause|workflow|impact|broader_scan|qualification|other",
    "root_cause_understood": true|false,
    "impact_understood": true|false
  }
Rules for conversation_focus:
- Describe the ASSISTANT question that the latest user message is answering (not the user's answer topic alone).
- Decide same_business_issue_as_previous semantically vs the previous assistant question / previous focus_label.
- question_stage is the purpose of that assistant question.
- Do not invent stages; use only the allowed enum values.
- Omit conversation_focus only if there is no assistant question being answered yet.

Allowed fields:
company, goals, revenue_range, headcount, functions, tools, workflows,
knowledge_locations, pain_points, time_cost_impact, bottlenecks,
change_readiness, ai_readiness, authority.

AUTHORITY (special value enum when status=known):
  "authority": {
    "status": "known|unknown|declined|not_applicable|missing",
    "value": "decision_maker|influencer|not_decision_maker|unknown",
    "evidence_quote": "<exact quote from a USER message>"
  }
Authority value meanings (semantic — do not use keyword shortcuts):
- decision_maker: user can make / finalize the purchase or change decision
- influencer: user recommends or shapes the decision but someone else signs off
- not_decision_maker: user clearly cannot decide / needs others with no influence path stated as primary
- unknown: user does not know who decides
status=unknown (field semantics) also applies when they cannot determine it; value=null then.

REVENUE_RANGE (when status=known, normalize to one of these exact bands):
  "$3–5M" | "$5–10M" | "$10–25M" | "$25–50M" | "$50M+"
- Map the user's stated size to the nearest matching band (semantic understanding).
- Do not invent a band without user evidence.

Also include:
  "assessment_sufficient": true|false
  (true only if a useful CAIO snapshot can be produced without guessing)

CRITICAL RULES:
- OMIT a field only when no USER message in the conversation gives evidence for it.
- Do NOT set status "unknown" merely because information is absent.
- Absence means leave the field out of the JSON so it stays missing.
- Never clear a previously known concrete value with a bare null / missing write.
- Do not guess.
""".strip()

HEADCOUNT_SCOPES = frozenset({"company", "team", "unknown"})

# Canonical acquisition revenue bands (format enum — not NL inference).
REVENUE_RANGES = (
    "$3–5M",
    "$5–10M",
    "$10–25M",
    "$25–50M",
    "$50M+",
)
REVENUE_RANGE_SET = frozenset(REVENUE_RANGES)

# Format aliases → canonical band (punctuation/spacing only; no business inference).
_REVENUE_ALIAS_KEYS: dict[str, str] = {
    "3-5m": "$3–5M",
    "3m-5m": "$3–5M",
    "5-10m": "$5–10M",
    "5m-10m": "$5–10M",
    "10-25m": "$10–25M",
    "10m-25m": "$10–25M",
    "25-50m": "$25–50M",
    "25m-50m": "$25–50M",
    "50m+": "$50M+",
    "50+m": "$50M+",
    "50mplus": "$50M+",
}


def normalize_revenue_range(value: Any) -> str | None:
    """Map a revenue string to a canonical band, or None if not a known band."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw in REVENUE_RANGE_SET:
        return raw
    key = raw.casefold()
    key = key.replace("—", "-").replace("–", "-")
    key = re.sub(r"\s+", "", key)
    key = key.replace("$", "")
    key = key.replace("million", "m").replace("plus", "+")
    return _REVENUE_ALIAS_KEYS.get(key)


AUTHORITY_ROLES = frozenset(
    {
        "decision_maker",
        "influencer",
        "not_decision_maker",
        "unknown",
    }
)

QUESTION_STAGES = frozenset(
    {
        "business_context",
        "problem_discovery",
        "root_cause",
        "workflow",
        "impact",
        "broader_scan",
        "qualification",
        "other",
    }
)
DEPTH_QUESTION_STAGES = frozenset({"problem_discovery", "root_cause", "workflow"})
CONVERSATION_FOCUS_STATE_KEY = "_conversation_focus_state"

STRUCTURED_KEYS = STRUCTURED_FIELD_KEYS
_READY_LINE = re.compile(rf"(?im)^\s*{ASSESSMENT_READY_MARKER}\s*$")

# Must-have fields: the assessment ends when all of these are resolved (resolved ≠ missing).
# They cover the qualification criteria plus what the result and the pre-call brief need.
REQUIRED_ASSESSMENT_FIELDS = (
    "company",
    "goals",
    "revenue_range",
    "headcount",
    "functions",
    "workflows",
    "pain_points",
    "time_cost_impact",
    "bottlenecks",
    "change_readiness",
    "ai_readiness",
    "authority",
)
# Nice-to-have fields: asked only when they fit naturally; never block finishing.
OPTIONAL_ASSESSMENT_FIELDS = tuple(
    key for key in STRUCTURED_FIELD_KEYS if key not in REQUIRED_ASSESSMENT_FIELDS
)

# What the prospect sees: topics with a tick once every field in the topic has an answer.
# A tick means "we got the answer"; it says nothing about qualification.
PROGRESS_TOPICS = (
    ("business", "Your business", ("company", "goals", "functions")),
    ("size", "Size and scale", ("revenue_range", "headcount")),
    ("operations", "How work runs", ("workflows", "bottlenecks")),
    ("problems", "Problems and cost", ("pain_points", "time_cost_impact")),
    ("readiness", "Readiness for change", ("change_readiness", "ai_readiness")),
    ("decision", "Decision making", ("authority",)),
)

# Fixed opening. Completion uses required-field coverage + semantic focus depth.
ASSESSMENT_OPENING_QUESTION = (
    "Tell me about your business and what you're trying to accomplish right now."
)
ASSESSMENT_CLOSING_MESSAGE = (
    "Thanks — I have enough to share a directional snapshot based on what you shared."
)


def count_assessment_questions(messages: list[Any] | None) -> int:
    """Opening assistant prompt counts as question 1; each later assistant turn is +1."""
    if not messages:
        return 0
    count = 0
    for message in messages:
        role = getattr(message, "role", None)
        role_value = role.value if hasattr(role, "value") else role
        if isinstance(message, dict):
            role_value = message.get("role")
        if str(role_value) == "assistant":
            count += 1
    return count


def empty_conversation_focus_state() -> dict[str, Any]:
    return {
        "current_focus_label": None,
        "same_business_issue_as_previous": None,
        "consecutive_same_issue_depth_count": 0,
        "impact_asked_after_depth": False,
        "force_change_business_issue": False,
        "allowed_next_stage": "any",
        "last_question_stage": None,
        "root_cause_understood": None,
        "impact_understood": None,
        "available": True,
    }


def get_conversation_focus_state(structured_state: dict[str, Any] | None) -> dict[str, Any]:
    raw = (structured_state or {}).get(CONVERSATION_FOCUS_STATE_KEY)
    if not isinstance(raw, dict):
        return empty_conversation_focus_state()
    base = empty_conversation_focus_state()
    base.update({k: raw[k] for k in base if k in raw})
    return base


def set_conversation_focus_state(
    structured_state: dict[str, Any] | None,
    focus_state: dict[str, Any],
) -> dict[str, Any]:
    data = dict(structured_state or {})
    data[CONVERSATION_FOCUS_STATE_KEY] = focus_state
    return data


def validate_conversation_focus(raw: Any) -> dict[str, Any] | None:
    """Validate extractor conversation_focus; None if malformed (no keyword fallback)."""
    if not isinstance(raw, dict):
        return None
    label = raw.get("focus_label")
    if not isinstance(label, str) or not label.strip():
        return None
    same = raw.get("same_business_issue_as_previous")
    if not isinstance(same, bool):
        return None
    stage = str(raw.get("question_stage") or "").strip().lower()
    if stage not in QUESTION_STAGES:
        return None
    root = raw.get("root_cause_understood")
    impact = raw.get("impact_understood")
    if not isinstance(root, bool) or not isinstance(impact, bool):
        return None
    return {
        "focus_label": label.strip(),
        "same_business_issue_as_previous": same,
        "question_stage": stage,
        "root_cause_understood": root,
        "impact_understood": impact,
    }


def apply_conversation_focus_metadata(
    previous_state: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Update focus tracking from validated semantic metadata (or mark unavailable).

    Depth is semantic only: dig until root cause + impact are understood, then move on.
    There is no fixed maximum follow-up count per business issue.
    """
    state = empty_conversation_focus_state()
    prev = previous_state or empty_conversation_focus_state()
    if metadata is None:
        state["available"] = False
        state["force_change_business_issue"] = False
        state["allowed_next_stage"] = "any"
        # Preserve last known label for context only; do not invent continuity.
        state["current_focus_label"] = prev.get("current_focus_label")
        state["consecutive_same_issue_depth_count"] = int(
            prev.get("consecutive_same_issue_depth_count") or 0
        )
        state["impact_asked_after_depth"] = bool(prev.get("impact_asked_after_depth"))
        state["root_cause_understood"] = prev.get("root_cause_understood")
        state["impact_understood"] = prev.get("impact_understood")
        logger.warning("conversation_focus metadata missing or invalid; topic enforcement skipped")
        return state

    state["available"] = True
    state["current_focus_label"] = metadata["focus_label"]
    state["same_business_issue_as_previous"] = metadata["same_business_issue_as_previous"]
    state["last_question_stage"] = metadata["question_stage"]
    state["root_cause_understood"] = metadata["root_cause_understood"]
    state["impact_understood"] = metadata["impact_understood"]

    stage = metadata["question_stage"]
    same = metadata["same_business_issue_as_previous"]
    prev_depth = int(prev.get("consecutive_same_issue_depth_count") or 0)
    prev_impact_asked = bool(prev.get("impact_asked_after_depth"))

    # Informational continuity only — never used as a hard depth limit.
    if not same:
        state["consecutive_same_issue_depth_count"] = 1 if stage in DEPTH_QUESTION_STAGES else 0
        state["impact_asked_after_depth"] = False
    else:
        state["consecutive_same_issue_depth_count"] = prev_depth
        state["impact_asked_after_depth"] = prev_impact_asked
        if stage in DEPTH_QUESTION_STAGES:
            state["consecutive_same_issue_depth_count"] = prev_depth + 1
        elif stage == "impact":
            state["impact_asked_after_depth"] = True

    # Move on only when root cause and impact are both sufficiently understood.
    state["force_change_business_issue"] = False
    state["allowed_next_stage"] = "any"
    if metadata["root_cause_understood"] and metadata["impact_understood"]:
        state["force_change_business_issue"] = True
        state["allowed_next_stage"] = "broader_or_new"

    return state


def same_issue_depth_permitted(focus_state: dict[str, Any] | None) -> bool:
    """Whether another depth question on the same issue is allowed next.

    Allowed while root cause or impact remains unclear. Blocked only when both are
    understood (force_change_business_issue). No fixed follow-up count.
    """
    state = focus_state or empty_conversation_focus_state()
    if not state.get("available", True):
        return True
    if state.get("force_change_business_issue"):
        return False
    return True


def meaningful_problem_identified(structured_state: dict[str, Any] | None) -> bool:
    """True when conversation_focus indicates a material business issue is in play."""
    focus = get_conversation_focus_state(structured_state)
    if not focus.get("available", True):
        return False
    label = focus.get("current_focus_label")
    if not isinstance(label, str) or not label.strip():
        return False
    stage = focus.get("last_question_stage")
    if stage in {"problem_discovery", "root_cause", "workflow", "impact"}:
        return True
    if focus.get("root_cause_understood") is not None or focus.get("impact_understood") is not None:
        return True
    return False


def diagnostic_depth_satisfied(structured_state: dict[str, Any] | None) -> bool:
    """When a material problem exists, root cause + impact must both be understood."""
    if not meaningful_problem_identified(structured_state):
        return True
    focus = get_conversation_focus_state(structured_state)
    return bool(focus.get("root_cause_understood")) and bool(focus.get("impact_understood"))


def missing_required_assessment_fields(state: dict[str, Any] | None) -> list[str]:
    """Required fields still status=missing (known/unknown/declined/N/A count as resolved)."""
    data = normalize_structured_state(state)
    return [key for key in REQUIRED_ASSESSMENT_FIELDS if not is_resolved(data[key])]


def required_assessment_coverage(state: dict[str, Any] | None) -> bool:
    """True when every required assessment field is resolved (not missing)."""
    return not missing_required_assessment_fields(state)


def useful_diagnostic_coverage(state: dict[str, Any] | None) -> bool:
    """Alias — required-field coverage (depth checked separately for completion)."""
    return required_assessment_coverage(state)


def backend_coverage_satisfied(state: dict[str, Any] | None) -> bool:
    """Alias used by completion services — required-field coverage check."""
    return required_assessment_coverage(state)


def build_current_assessment_state(structured_state: dict[str, Any] | None) -> dict[str, Any]:
    """Hidden CURRENT ASSESSMENT STATE payload for the conversational AI."""
    data = normalize_structured_state(structured_state)
    focus = get_conversation_focus_state(structured_state)
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for key in REQUIRED_ASSESSMENT_FIELDS:
        entry = data[key]
        if is_resolved(entry):
            resolved[key] = {
                "status": entry.get("status"),
                "value": entry.get("value"),
            }
            if entry.get("source"):
                resolved[key]["source"] = entry["source"]
        else:
            missing.append(key)
    return {
        "resolved_fields": resolved,
        "missing_fields": missing,
        # Nice to have: ask only if it fits naturally; never needed to finish.
        "optional_missing_fields": [
            key for key in OPTIONAL_ASSESSMENT_FIELDS if not is_resolved(data[key])
        ],
        # Alias retained for any prompt/tests that still say unresolved_fields.
        "unresolved_fields": missing,
        "current_business_issue": focus.get("current_focus_label"),
        "root_cause_understanding": focus.get("root_cause_understood"),
        "impact_understanding": focus.get("impact_understood"),
        "business_issue_context": {
            "focus_label": focus.get("current_focus_label"),
            "root_cause_understood": focus.get("root_cause_understood"),
            "impact_understood": focus.get("impact_understood"),
            "consecutive_same_issue_depth_count": focus.get(
                "consecutive_same_issue_depth_count"
            ),
            "last_question_stage": focus.get("last_question_stage"),
            "allowed_next_stage": focus.get("allowed_next_stage"),
            "force_change_business_issue": focus.get("force_change_business_issue"),
        },
    }


def assessment_progress(state: dict[str, Any] | None) -> dict[str, Any]:
    """Prospect-safe progress: topics with a tick once every field in the topic has an answer.

    No field names, values or qualification signals are exposed. A tick only means
    "we got the answer".
    """
    data = normalize_structured_state(state)
    topics = [
        {
            "key": key,
            "label": label,
            "done": all(is_resolved(data[name]) for name in fields),
        }
        for key, label, fields in PROGRESS_TOPICS
    ]
    answered = sum(1 for t in topics if t["done"])
    required = list(REQUIRED_ASSESSMENT_FIELDS)
    got = sum(1 for key in required if is_resolved(data[key]))
    return {
        "topics": topics,
        "answered": answered,
        "total": len(topics),
        "remaining": len(topics) - answered,
        "percent": int(round(100 * got / len(required))) if required else 100,
    }


def is_ready_for_completion(
    state: dict[str, Any] | None,
    *,
    turn_meta: dict[str, Any] | None = None,
    question_count: int = 0,
    force_hard_limit: bool = False,
    ai_sufficient: bool = False,
    assistant_ready_signal: bool = False,
    assistant_message: str | None = None,
) -> bool:
    """Complete as soon as every must-have field has an answer.

    The must-haves already include the problems, their cost and the bottlenecks, so there is no
    separate root-cause / impact gate. Nice-to-have fields never block finishing.
    No question-count gate; no assessment_ready flag.
    """
    del turn_meta, question_count, force_hard_limit, ai_sufficient
    del assistant_ready_signal, assistant_message
    return required_assessment_coverage(state)


def empty_assistant_turn_meta(*, available: bool = True) -> dict[str, Any]:
    return {
        "assistant_message": "",
        "available": available,
    }


def validate_assistant_turn(raw: Any) -> dict[str, Any] | None:
    """Validate next-question turn JSON. Completion is backend field-state, not model flags."""
    if not isinstance(raw, dict):
        return None
    message = raw.get("assistant_message")
    if not isinstance(message, str) or not message.strip():
        return None
    return {
        "assistant_message": message.strip(),
        "available": True,
    }


def parse_assistant_turn_response(raw: str) -> dict[str, Any]:
    """Parse model next-question JSON; on failure keep a usable visible message."""
    data = parse_extractor_json(raw)
    validated = validate_assistant_turn(data)
    if validated is not None:
        return validated

    logger.warning(
        "assistant turn metadata malformed; using fallback message; raw_prefix=%r",
        (raw or "")[:240],
    )
    message = ""
    if isinstance(data, dict) and isinstance(data.get("assistant_message"), str):
        message = data["assistant_message"].strip()
    if not message:
        cleaned, _ = strip_assessment_ready_marker(raw or "")
        message = cleaned.strip()
    if not message or message.startswith("{"):
        message = "Thanks — could you share a bit more about that?"
    meta = empty_assistant_turn_meta(available=False)
    meta["assistant_message"] = message
    return meta


def normalize_field(raw: Any) -> dict[str, Any]:
    """Normalize legacy flat values or dict entries into {value, status[, source, ...]}."""
    if isinstance(raw, dict) and "status" in raw:
        status = str(raw.get("status") or FieldStatus.MISSING.value).strip().lower()
        if status not in {s.value for s in FieldStatus}:
            status = FieldStatus.MISSING.value
        value = raw.get("value")
        if status == FieldStatus.KNOWN.value and _is_empty(value):
            status = FieldStatus.MISSING.value
        out: dict[str, Any] = {
            "value": value if not _is_empty(value) else None,
            "status": status,
        }
        source = raw.get("source")
        if source in {FIELD_SOURCE_PROSPECT_SEED, FIELD_SOURCE_ASSESSMENT}:
            out["source"] = source
        # Optional provenance metadata (headcount and future fields). Absent on legacy rows.
        if raw.get("evidence_quote") is not None:
            out["evidence_quote"] = raw.get("evidence_quote")
        if raw.get("evidence_message_id") is not None:
            out["evidence_message_id"] = str(raw.get("evidence_message_id"))
        scope = raw.get("scope")
        if scope in HEADCOUNT_SCOPES:
            out["scope"] = scope
        return out
    if _is_empty(raw):
        return empty_field()
    return {"value": raw, "status": FieldStatus.KNOWN.value}


def normalize_structured_state(state: dict[str, Any] | None) -> dict[str, Any]:
    base = empty_structured_state()
    if not isinstance(state, dict):
        return base
    for key in STRUCTURED_FIELD_KEYS:
        if key in state:
            base[key] = normalize_field(state[key])
    return base


def field_display(entry: Any) -> dict[str, Any]:
    """Display helpers for a structured field.

    Returns status enum value plus a human-readable status_label.
    Does NOT include a field-name label — callers supply that separately so it is
    never overwritten by the status title.
    """
    norm = normalize_field(entry)
    status = norm["status"]
    return {
        "value": norm["value"],
        "status": status,
        "status_label": str(status).replace("_", " ").title(),
        "source": norm.get("source"),
    }


def concrete_value(entry: Any) -> Any:
    norm = normalize_field(entry)
    if norm["status"] == FieldStatus.KNOWN.value:
        return norm["value"]
    return None


def is_resolved(entry: Any) -> bool:
    """True when the field is no longer missing (known / unknown / declined / N/A)."""
    return normalize_field(entry)["status"] != FieldStatus.MISSING.value


def is_known(entry: Any) -> bool:
    return normalize_field(entry)["status"] == FieldStatus.KNOWN.value


def sanitize_extraction_patch(
    patch: dict[str, Any] | None,
    *,
    latest_user_message: str,
    existing: dict[str, Any] | None = None,
    user_message_id: str | None = None,
    user_message_role: str | None = None,
    user_messages: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Drop invented / unevidenced extractor updates before merge.

    All assessment fields require grounded evidence_quote (no NL keyword gates).
    The quote may come from the latest user message or from any earlier user message
    (``user_messages`` = [(message_id, text), ...] oldest first); provenance points at the
    message that really contains it. Headcount keeps additional scope=company protection.
    """
    if not isinstance(patch, dict):
        return {}
    current = normalize_structured_state(existing)
    user_text = latest_user_message or ""
    pool = list(user_messages or [])
    cleaned: dict[str, Any] = {}

    def _source(entry: Any) -> tuple[str, str | None]:
        """The user message (text, id) that contains this entry's evidence quote."""
        quote = entry.get("evidence_quote") if isinstance(entry, dict) else None
        if isinstance(quote, str) and quote.strip():
            if evidence_quote_in_user_message(quote, user_text):
                return user_text, user_message_id
            for mid, text in reversed(pool):
                if evidence_quote_in_user_message(quote, text):
                    return text, mid
        return user_text, user_message_id

    if "assessment_sufficient" in patch:
        cleaned["assessment_sufficient"] = bool(patch.get("assessment_sufficient"))

    if "headcount" in patch:
        hc_text, hc_id = _source(patch.get("headcount"))
        headcount_entry = sanitize_headcount_extraction(
            patch.get("headcount"),
            user_message_text=hc_text,
            user_message_id=hc_id,
            user_message_role=user_message_role,
            existing_headcount=current.get("headcount"),
        )
        if headcount_entry is not None:
            cleaned["headcount"] = headcount_entry

    for key in STRUCTURED_FIELD_KEYS:
        if key == "headcount":
            continue
        if key not in patch:
            continue
        field_text, field_id = _source(patch.get(key))
        entry = sanitize_structured_field_extraction(
            patch.get(key),
            field_key=key,
            user_message_text=field_text,
            user_message_id=field_id,
            user_message_role=user_message_role,
        )
        if entry is not None:
            cleaned[key] = entry

    return cleaned


def normalize_evidence_text(text: str | None) -> str:
    """Case/whitespace/punctuation normalization for quote matching (not NL inference)."""
    folded = (text or "").casefold()
    # Allow letters, digits, currency/percent markers; treat other punctuation as spaces.
    cleaned = re.sub(r"[^\w\s$%]", " ", folded, flags=re.UNICODE)
    return " ".join(cleaned.split())


def evidence_quote_in_user_message(evidence_quote: str | None, user_message: str | None) -> bool:
    """True when evidence_quote occurs in the user message after normalization."""
    quote = normalize_evidence_text(evidence_quote)
    hay = normalize_evidence_text(user_message)
    if not quote or not hay:
        return False
    return quote in hay


def sanitize_structured_field_extraction(
    raw: Any,
    *,
    field_key: str,
    user_message_text: str,
    user_message_id: str | None,
    user_message_role: str | None,
) -> dict[str, Any] | None:
    """Validate evidence-backed structured field object; None = no write.

    Model decides status/value; backend only validates schema + evidence grounding.
    Does not discover business meaning via keywords/regex.
    """
    role = str(user_message_role or "").strip().lower()
    if role and role not in {"user", "prospect"}:
        logger.info(
            "Rejected %s: evidence source role is not user/prospect (%r)",
            field_key,
            role,
        )
        return None

    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.info(
            "Rejected %s: expected evidence object, got %s",
            field_key,
            type(raw).__name__,
        )
        return None

    # Model must not set provenance.
    raw = {k: v for k, v in raw.items() if k not in {"source", "evidence_message_id"}}

    status = str(raw.get("status") or FieldStatus.MISSING.value).strip().lower()
    if status not in {s.value for s in FieldStatus}:
        logger.info("Rejected %s: invalid status %r", field_key, status)
        return None
    if status == FieldStatus.MISSING.value:
        return None

    evidence_quote = raw.get("evidence_quote")
    if not isinstance(evidence_quote, str) or not evidence_quote.strip():
        logger.info("Rejected %s: evidence_quote required for status=%s", field_key, status)
        return None
    if not evidence_quote_in_user_message(evidence_quote, user_message_text):
        logger.info(
            "Rejected %s: evidence_quote not found in current user message",
            field_key,
        )
        return None
    if not user_message_id:
        logger.info("Rejected %s: user_message_id required for provenance", field_key)
        return None

    if status == FieldStatus.KNOWN.value:
        value = raw.get("value")
        # Authority: known value must be a controlled role enum (no free-text → decision_maker).
        if field_key == "authority":
            role_raw = value if not _is_empty(value) else raw.get("authority_role")
            role_norm = str(role_raw or "").strip().lower().replace(" ", "_").replace("-", "_")
            if role_norm not in AUTHORITY_ROLES:
                logger.info(
                    "Rejected authority: known status requires authority_role enum, got %r",
                    role_raw,
                )
                return None
            value = role_norm
        elif field_key == "revenue_range":
            band = normalize_revenue_range(value)
            if band is None:
                logger.info(
                    "Rejected revenue_range: known status requires canonical band, got %r",
                    value,
                )
                return None
            value = band
        elif _is_empty(value):
            logger.info("Rejected %s: known status requires non-empty value", field_key)
            return None
        return {
            "value": value,
            "status": FieldStatus.KNOWN.value,
            "source": FIELD_SOURCE_ASSESSMENT,
            "evidence_quote": evidence_quote.strip(),
            "evidence_message_id": str(user_message_id),
        }

    # unknown / declined / not_applicable
    if not _is_empty(raw.get("value")):
        logger.info("Rejected %s: %s status requires null value", field_key, status)
        return None
    return {
        "value": None,
        "status": status,
        "source": FIELD_SOURCE_ASSESSMENT,
        "evidence_quote": evidence_quote.strip(),
        "evidence_message_id": str(user_message_id),
    }


def _coerce_headcount_integer(value: Any) -> int | None:
    """Accept only a whole-number headcount (int or digit string). Reject bands/phrases."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def sanitize_headcount_extraction(
    raw: Any,
    *,
    user_message_text: str,
    user_message_id: str | None,
    user_message_role: str | None,
    existing_headcount: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate extractor headcount object; backend assigns source and evidence_message_id.

    Returns a merge-ready field dict, or None for no-write (missing / rejected).
    Does not discover headcount from English via regex.
    """
    role = str(user_message_role or "").strip().lower()
    if role and role != "user":
        logger.info("Rejected headcount: evidence source role is not user (%r)", role)
        return None

    if raw is None:
        return None
    # Bare strings/numbers are not the strict headcount schema — reject (no NL fallback).
    if not isinstance(raw, dict):
        logger.info("Rejected headcount: expected strict object, got %s", type(raw).__name__)
        return None

    # Model must not set provenance.
    raw = {k: v for k, v in raw.items() if k != "source"}

    status = str(raw.get("status") or FieldStatus.MISSING.value).strip().lower()
    if status not in {s.value for s in FieldStatus}:
        logger.info("Rejected headcount: invalid status %r", status)
        return None
    if status == FieldStatus.MISSING.value:
        return None

    evidence_quote = raw.get("evidence_quote")
    if not isinstance(evidence_quote, str) or not evidence_quote.strip():
        logger.info("Rejected headcount: evidence_quote required for status=%s", status)
        return None
    if not evidence_quote_in_user_message(evidence_quote, user_message_text):
        logger.info("Rejected headcount: evidence_quote not found in current user message")
        return None
    if not user_message_id:
        logger.info("Rejected headcount: user_message_id required for provenance")
        return None

    scope_raw = raw.get("scope")
    scope = str(scope_raw).strip().lower() if scope_raw is not None else "unknown"
    if scope not in HEADCOUNT_SCOPES:
        scope = "unknown"

    if status == FieldStatus.KNOWN.value:
        value = _coerce_headcount_integer(raw.get("value"))
        if value is None:
            logger.info("Rejected headcount: known status requires integer value")
            return None
        # Primary field is company headcount only. Team/unknown must never populate it.
        if scope != "company":
            logger.info(
                "Rejected headcount: scope=%r is not company; not writing primary headcount",
                scope,
            )
            return None
        return {
            "value": value,
            "status": FieldStatus.KNOWN.value,
            "source": FIELD_SOURCE_ASSESSMENT,
            "evidence_quote": evidence_quote.strip(),
            "evidence_message_id": str(user_message_id),
            "scope": "company",
        }

    # unknown / declined / not_applicable
    if not _is_empty(raw.get("value")):
        logger.info("Rejected headcount: %s status requires null value", status)
        return None
    return {
        "value": None,
        "status": status,
        "source": FIELD_SOURCE_ASSESSMENT,
        "evidence_quote": evidence_quote.strip(),
        "evidence_message_id": str(user_message_id),
        "scope": scope,
    }


def prospect_seed_value(value: Any) -> dict[str, Any]:
    """Trusted prospect/profile prefill tagged so assessment answers can override it."""
    if _is_empty(value):
        return empty_field()
    return {
        "value": value,
        "status": FieldStatus.KNOWN.value,
        "source": FIELD_SOURCE_PROSPECT_SEED,
    }


def merge_structured_state(
    existing: dict[str, Any] | None,
    patch: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge patch into existing without erasing known concrete values with empties.

    Precedence for the assessment snapshot:
    1. Explicit current-assessment answers (source=assessment) win.
    2. Trusted prospect prefill (source=prospect_seed) only fills missing fields.
    3. A newer assessment known value always overwrites an older prospect seed.
    """
    focus_state = None
    if isinstance(existing, dict) and CONVERSATION_FOCUS_STATE_KEY in existing:
        focus_state = existing.get(CONVERSATION_FOCUS_STATE_KEY)

    merged = normalize_structured_state(existing)
    if not isinstance(patch, dict):
        if focus_state is not None:
            merged[CONVERSATION_FOCUS_STATE_KEY] = focus_state
        return merged
    for key in STRUCTURED_FIELD_KEYS:
        if key not in patch:
            continue
        incoming = normalize_field(patch[key])
        current = merged[key]
        if incoming["status"] == FieldStatus.MISSING.value and _is_empty(incoming["value"]):
            continue
        if incoming["status"] == FieldStatus.KNOWN.value and _is_empty(incoming["value"]):
            continue
        incoming_source = incoming.get("source")
        current_source = current.get("source")

        # Prospect seed must not overwrite an assessment-confirmed value.
        if (
            incoming_source == FIELD_SOURCE_PROSPECT_SEED
            and current["status"] == FieldStatus.KNOWN.value
            and not _is_empty(current["value"])
            and current_source == FIELD_SOURCE_ASSESSMENT
        ):
            continue
        if (
            incoming_source == FIELD_SOURCE_PROSPECT_SEED
            and current["status"] != FieldStatus.MISSING.value
        ):
            continue

        if (
            current["status"] == FieldStatus.KNOWN.value
            and not _is_empty(current["value"])
            and incoming["status"]
            in {
                FieldStatus.UNKNOWN.value,
                FieldStatus.DECLINED.value,
                FieldStatus.NOT_APPLICABLE.value,
            }
        ):
            # Explicit clarification overrides a prior known value.
            merged[key] = {
                "value": None,
                "status": incoming["status"],
                "source": FIELD_SOURCE_ASSESSMENT,
            }
            continue
        if (
            current["status"] == FieldStatus.KNOWN.value
            and not _is_empty(current["value"])
            and incoming["status"] != FieldStatus.KNOWN.value
            and _is_empty(incoming["value"])
        ):
            continue
        if (
            incoming["status"] == FieldStatus.KNOWN.value
            and current["status"] == FieldStatus.KNOWN.value
            and not _is_empty(current["value"])
            and not _values_equivalent(current["value"], incoming["value"])
            and current_source == FIELD_SOURCE_PROSPECT_SEED
            and incoming_source != FIELD_SOURCE_PROSPECT_SEED
        ):
            logger.info(
                "Assessment field %s overrides prospect seed %r → %r",
                key,
                current["value"],
                incoming["value"],
            )
        if incoming["status"] != FieldStatus.MISSING.value and "source" not in incoming:
            # Default patched known/resolved values to assessment unless tagged.
            if incoming["status"] == FieldStatus.KNOWN.value or incoming["status"] in {
                FieldStatus.UNKNOWN.value,
                FieldStatus.DECLINED.value,
                FieldStatus.NOT_APPLICABLE.value,
            }:
                incoming = {**incoming, "source": FIELD_SOURCE_ASSESSMENT}
        merged[key] = incoming

    if focus_state is not None:
        merged[CONVERSATION_FOCUS_STATE_KEY] = focus_state
    return merged


def parse_extractor_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            logger.warning("Assessment extractor returned non-JSON")
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            logger.warning("Assessment extractor JSON parse failed")
            return {}
    return data if isinstance(data, dict) else {}


def strip_assessment_ready_marker(text: str) -> tuple[str, bool]:
    """Deprecated: strip ASSESSMENT_READY line if present (backward compat only).

    Completion uses required-field coverage only; this marker is not a completion signal.
    """
    original = text or ""
    signaled = bool(_READY_LINE.search(original))
    cleaned = _READY_LINE.sub("", original)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, signaled


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _values_equivalent(a: Any, b: Any) -> bool:
    return str(a).strip().lower() == str(b).strip().lower()
