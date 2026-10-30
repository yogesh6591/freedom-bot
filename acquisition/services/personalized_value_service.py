"""Generate and persist prospect-specific Value opportunity copy.

Grounded in assessment evidence only. Never invents pricing/ROI.
Investment/price wording stays Admin-controlled elsewhere.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from acquisition.config import get_settings
from acquisition.models.assessment import (
    AssessmentConversation,
    AssessmentMessageRole,
    AssessmentProfile,
)
from acquisition.models.campaign import CampaignProspect
from acquisition.services.assessment_extraction import field_display, normalize_structured_state

logger = logging.getLogger(__name__)

PERSONALIZED_VALUE_MODEL = "gpt-4o-mini"

PERSONALIZED_VALUE_INSTRUCTIONS = """
You write ONE short prospect-facing Value opportunity section for FreedomBot.

Return ONLY JSON:
{
  "personalized_value": "<one or two short paragraphs>"
}

Use ONLY the provided assessment evidence:
- inefficiencies_summary / pain points / bottlenecks
- measurable delay/rework/time impact when present
- opportunity_areas (Where FreedomBot may help)

Target style (adapt to THIS prospect's evidence — do not copy blindly):
"Your assessment showed that sales-to-delivery handoffs are creating missed context,
owner escalation, and 10–15 hours of waiting/rework on a recent project. The strongest
value opportunity is reducing that coordination overhead and making guidance easier
for the team to access without repeatedly depending on leadership."

Rules:
- Prefer "Your assessment showed…" opening when natural.
- Name the operational friction and the strongest value opportunity.
- Include concrete time/delay/rework figures ONLY when present in evidence.
- Ground every claim in the evidence. Prefer cautious wording when needed.
- Do NOT invent ROI, dollar savings, prices, packages, or investment amounts.
- Do NOT invent tools, metrics, headcount, or outcomes that are not in the evidence.
- Do NOT pitch FreedomBot features by name in a salesy way; describe the opportunity.
- Do NOT mention Admin, TEST content, budget readiness, calendar unlock, or internal gates.
- Do NOT use regex/keyword stuffing or generic capability menus.
""".strip()

_PRICE_LEAK = re.compile(
    r"(\$|usd\b|roi\b|return on investment|save[sd]?\s+\d|%\s*savings?|"
    r"starter|growth|scale\s+plan|pricing package)",
    re.IGNORECASE,
)


def _user_messages(conversation: AssessmentConversation | None) -> list[str]:
    if conversation is None:
        return []
    out: list[str] = []
    for msg in conversation.messages or []:
        if msg.role == AssessmentMessageRole.USER and (msg.content or "").strip():
            out.append(msg.content.strip())
    return out


def _evidence_bundle(
    *,
    profile: AssessmentProfile,
    link: CampaignProspect,
    conversation: AssessmentConversation | None,
) -> dict[str, Any]:
    result = profile.result_payload if isinstance(profile.result_payload, dict) else {}
    state = normalize_structured_state(profile.structured_state)
    areas = result.get("opportunity_areas") or []
    area_labels: list[str] = []
    if isinstance(areas, list):
        for item in areas:
            if isinstance(item, dict) and isinstance(item.get("label"), str):
                label = item["label"].strip()
                if label:
                    area_labels.append(label)
            elif isinstance(item, str) and item.strip():
                area_labels.append(item.strip())

    def _field_value(key: str) -> Any:
        return field_display(state.get(key)).get("value")

    return {
        "company": (link.prospect.company if link.prospect else None) or "unknown",
        "executive_name": (link.prospect.executive_name if link.prospect else None),
        "inefficiencies_summary": result.get("inefficiencies_summary"),
        "observed_impact": result.get("observed_impact") or result.get("directional_impact"),
        "opportunity_areas": area_labels,
        "pain_points": _field_value("pain_points"),
        "bottlenecks": _field_value("bottlenecks"),
        "time_cost_impact": _field_value("time_cost_impact"),
        "workflows": _field_value("workflows"),
        "user_evidence_excerpts": _user_messages(conversation)[-8:],
    }


def grounded_personalized_value_fallback(evidence: dict[str, Any]) -> str:
    """Stable non-LLM composition from already-grounded assessment result fields."""
    ineff = str(evidence.get("inefficiencies_summary") or "").strip()
    impact = str(evidence.get("observed_impact") or "").strip()
    areas = evidence.get("opportunity_areas") or []
    if not isinstance(areas, list):
        areas = []
    has_areas = any(str(a).strip() for a in areas)

    sentences: list[str] = []
    if ineff:
        lower = ineff.lower()
        if lower.startswith("your assessment showed"):
            lead = ineff
        elif lower.startswith("your "):
            lead = f"Your assessment showed that {ineff[5:].lstrip()}"
        else:
            lead = f"Your assessment showed that {ineff[0].lower()}{ineff[1:]}"
        sentences.append(lead if lead.endswith((".", "!", "?")) else f"{lead}.")
    if impact:
        sentences.append(impact if impact.endswith((".", "!", "?")) else f"{impact}.")
    if has_areas or ineff:
        sentences.append(
            "The strongest value opportunity is reducing that coordination overhead "
            "and making guidance easier for the team to access without repeatedly "
            "depending on leadership."
        )
    text = " ".join(sentences).strip()
    if not text:
        text = (
            "Your assessment highlighted operational friction that FreedomBot may help "
            "reduce by consolidating guidance and improving how work moves between teams."
        )
    return text


def _sanitize_personalized_text(text: str, *, evidence: dict[str, Any]) -> str | None:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return None
    # Block price/ROI inventions unless the same token already appears in evidence.
    evidence_blob = json.dumps(evidence, default=str).lower()
    for match in _PRICE_LEAK.finditer(cleaned):
        token = match.group(0).lower().strip()
        if token and token not in evidence_blob and "$" not in evidence_blob:
            logger.info("Rejected personalized value for price/ROI leak: %r", token)
            return None
    # Soft length guard — keep it a short section.
    if len(cleaned) > 900:
        cleaned = cleaned[:897].rsplit(" ", 1)[0] + "…"
    return cleaned


def generate_personalized_value_text(
    *,
    evidence: dict[str, Any],
    openai_call: Callable[[list[dict[str, str]]], str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (text, meta). Prefer OpenAI; fall back to grounded composition."""
    meta: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "fallback",
        "model": None,
        "evidence_keys": sorted(k for k, v in evidence.items() if v not in (None, "", [], {})),
    }
    if openai_call is not None:
        try:
            raw = openai_call(
                [
                    {"role": "system", "content": PERSONALIZED_VALUE_INSTRUCTIONS},
                    {
                        "role": "user",
                        "content": (
                            "Assessment evidence (JSON):\n"
                            f"{json.dumps(evidence, default=str)}"
                        ),
                    },
                ]
            )
            from acquisition.services.assessment_extraction import parse_extractor_json

            parsed = parse_extractor_json(raw)
            candidate = None
            if isinstance(parsed, dict):
                candidate = parsed.get("personalized_value") or parsed.get("text")
            if isinstance(candidate, str):
                sanitized = _sanitize_personalized_text(candidate, evidence=evidence)
                if sanitized:
                    meta["source"] = "openai"
                    meta["model"] = PERSONALIZED_VALUE_MODEL
                    return sanitized, meta
        except Exception as exc:  # noqa: BLE001
            logger.warning("Personalized value OpenAI failed: %s", type(exc).__name__)

    fallback = grounded_personalized_value_fallback(evidence)
    sanitized = _sanitize_personalized_text(fallback, evidence=evidence) or fallback
    return sanitized, meta


class PersonalizedValueService:
    def __init__(
        self,
        db: Session,
        *,
        openai_call: Callable[[list[dict[str, str]]], str] | None = None,
    ) -> None:
        self.db = db
        self._openai_call = openai_call

    def ensure_for_profile(
        self,
        link: CampaignProspect,
        profile: AssessmentProfile,
        conversation: AssessmentConversation | None = None,
        *,
        force: bool = False,
    ) -> str:
        """Generate once and persist; return stable text."""
        existing = (profile.personalized_value_text or "").strip()
        if existing and not force:
            return existing

        evidence = _evidence_bundle(profile=profile, link=link, conversation=conversation)
        openai_call = self._openai_call or self._default_openai_call
        text, meta = generate_personalized_value_text(
            evidence=evidence,
            openai_call=openai_call,
        )
        profile.personalized_value_text = text
        profile.personalized_value_meta = meta
        flag_modified(profile, "personalized_value_meta")
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return text

    def _default_openai_call(
        self, input_messages: list[dict[str, str]]
    ) -> str:
        settings = get_settings()
        api_key = (settings.openai_api_key or "").strip()
        if not api_key:
            raise RuntimeError("no_openai")
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        # Prefer chat.completions for broad compatibility in this helper path.
        response = client.chat.completions.create(
            model=PERSONALIZED_VALUE_MODEL,
            messages=input_messages,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return (response.choices[0].message.content or "").strip()
