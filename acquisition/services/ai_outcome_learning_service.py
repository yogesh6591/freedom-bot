"""AI-assisted outcome prioritization — bounded priority adjustment via OpenAI.

Deterministic historical metrics stay in AcquisitionLearningService.
This service interprets supplied prospect + comparable outcome facts only.
Never mutates Fit / Timing / Reachability / base Priority.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

PROMPT_VERSION = "outcome-learning-v2"

SYSTEM_PROMPT = """You are FreedomBot's AI-assisted outcome prioritization analyst.

Your task is to assess whether historical business outcomes from comparable
previously contacted prospects provide enough evidence to slightly increase,
decrease, or leave unchanged the priority of the supplied prospect.

Use only the supplied structured historical data and comparable prospects.

Do not invent data.
Do not require an exact company match — compare by characteristics.
Do not assume correlation is causation.
Do not reward vanity metrics such as opens or clicks.
Prioritize evidence from qualification, booked meetings, purchases and revenue.

Small or conflicting samples should result in a neutral or very small
adjustment.

Never modify the base Fit, Timing, Reachability or Priority scores.

Return only the required JSON.
Do not return chain-of-thought."""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

ALLOWED_MODELS = (
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
)


@dataclass(frozen=True, slots=True)
class AiOutcomeLearningResult:
    adjustment: float
    confidence: float
    reason: str
    matched_patterns: list[dict[str, Any]]
    historical_sample_size: int
    insufficient_evidence: bool
    model: str
    prompt_version: str
    ok: bool
    error: str | None = None
    openai_called: bool = False
    # Back-compat alias used by older callers/tests.
    supporting_patterns: list[dict[str, Any]] = field(default_factory=list)


class AiOutcomeLearningService:
    """Call a configurable OpenAI model for a bounded learning adjustment."""

    def __init__(
        self,
        db: Session | None = None,
        *,
        settings: Settings | None = None,
        openai_call: Callable[[list[dict[str, str]], str], str] | None = None,
        model_override: str | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._openai_call = openai_call
        self._model_override = (model_override or "").strip() or None

    def model_name(self) -> str:
        chosen = (
            self._model_override
            or (self.settings.outcome_learning_model or "").strip()
            or (self.settings.openai_model or "").strip()
            or "gpt-4o-mini"
        )
        return chosen.strip() or "gpt-4o-mini"

    def openai_available(self) -> bool:
        openai = OpenAIService(db=self.db, settings=self.settings)
        key, _model = openai.resolve_credentials()
        if key:
            return True
        return bool((self.settings.openai_api_key or "").strip())

    def can_invoke(self) -> bool:
        return self._openai_call is not None or self.openai_available()

    def recommend_adjustment(
        self,
        *,
        prospect_payload: dict[str, Any],
        historical_summary: dict[str, Any],
        maximum_adjustment: float,
    ) -> AiOutcomeLearningResult:
        model = self.model_name()
        max_adj = abs(float(maximum_adjustment or 8.0))
        comparable = historical_summary.get("comparable_prospects") or []
        sample_hint = int(
            historical_summary.get("historical_sample_size")
            or len(comparable)
            or (historical_summary.get("totals") or {}).get("contacted")
            or 0
        )
        payload = {
            "prospect": prospect_payload,
            "historical_summary": {
                "totals": historical_summary.get("totals") or {},
                "baselines": historical_summary.get("baselines") or {},
                "relevant_cohorts": historical_summary.get("relevant_cohorts") or {},
                "comparable_prospects": comparable,
                "historical_sample_size": sample_hint,
            },
            "limits": {"maximum_adjustment": max_adj},
            "prompt_version": PROMPT_VERSION,
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Analyze the structured payload and return ONLY this JSON shape:\n"
                    "{\n"
                    '  "priority_adjustment": <number between -{max} and +{max}>,\n'
                    '  "confidence": <number 0..1>,\n'
                    '  "reason": "<short business-readable explanation>",\n'
                    '  "matched_patterns": [\n'
                    "    {\n"
                    '      "dimension": "<string>",\n'
                    '      "value": "<string>",\n'
                    '      "sample": <int>,\n'
                    '      "observation": "<short string>"\n'
                    "    }\n"
                    "  ],\n"
                    '  "historical_sample_size": <int>,\n'
                    '  "insufficient_evidence": <true|false>\n'
                    "}\n"
                    "Maximum 3 matched_patterns. If insufficient_evidence is true, "
                    "priority_adjustment must be 0. historical_sample_size must reflect "
                    "the comparable set size you actually used from the payload. "
                    "Every statistic you cite must exist in the payload.\n\n"
                    f"PAYLOAD:\n{json.dumps(payload, default=str)}"
                ).replace("{max}", f"{max_adj:g}"),
            },
        ]
        try:
            raw = (self._openai_call or self._default_openai_call)(messages, model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI outcome learning OpenAI call failed: %s", exc.__class__.__name__)
            return AiOutcomeLearningResult(
                adjustment=0.0,
                confidence=0.0,
                reason="AI learning unavailable — base priority scoring continues normally.",
                matched_patterns=[],
                supporting_patterns=[],
                historical_sample_size=sample_hint,
                insufficient_evidence=True,
                model=model,
                prompt_version=PROMPT_VERSION,
                ok=False,
                error=exc.__class__.__name__,
                openai_called=True,
            )

        parsed = self._parse_json(raw)
        if parsed is None:
            return AiOutcomeLearningResult(
                adjustment=0.0,
                confidence=0.0,
                reason="AI learning response was invalid — using base priority.",
                matched_patterns=[],
                supporting_patterns=[],
                historical_sample_size=sample_hint,
                insufficient_evidence=True,
                model=model,
                prompt_version=PROMPT_VERSION,
                ok=False,
                error="malformed_json",
                openai_called=True,
            )
        return self._normalize(
            parsed,
            model=model,
            maximum_adjustment=max_adj,
            fallback_sample=sample_hint,
        )

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
            temperature=0.0,
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

    @staticmethod
    def _normalize(
        data: dict[str, Any],
        *,
        model: str,
        maximum_adjustment: float,
        fallback_sample: int,
    ) -> AiOutcomeLearningResult:
        insufficient = bool(data.get("insufficient_evidence"))
        raw_adj = data.get("priority_adjustment", data.get("adjustment"))
        try:
            adjustment = float(raw_adj)
        except (TypeError, ValueError):
            return AiOutcomeLearningResult(
                adjustment=0.0,
                confidence=0.0,
                reason="AI learning response was invalid — using base priority.",
                matched_patterns=[],
                supporting_patterns=[],
                historical_sample_size=max(0, int(fallback_sample or 0)),
                insufficient_evidence=True,
                model=model,
                prompt_version=PROMPT_VERSION,
                ok=False,
                error="invalid_adjustment",
                openai_called=True,
            )
        try:
            confidence = float(data.get("confidence") if data.get("confidence") is not None else 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        reason = str(data.get("reason") or "").strip() or (
            "No clear outcome pattern; leaving priority unchanged."
        )
        patterns_raw = data.get("matched_patterns") or data.get("supporting_patterns") or []
        patterns: list[dict[str, Any]] = []
        if isinstance(patterns_raw, list):
            for item in patterns_raw[:3]:
                if not isinstance(item, dict):
                    continue
                try:
                    sample = int(item.get("sample") or 0)
                except (TypeError, ValueError):
                    sample = 0
                patterns.append(
                    {
                        "dimension": str(item.get("dimension") or "")[:64],
                        "value": str(item.get("value") or "")[:128],
                        "sample": max(0, sample),
                        "observation": str(item.get("observation") or "")[:240],
                    }
                )
        try:
            hist_n = int(
                data.get("historical_sample_size")
                if data.get("historical_sample_size") is not None
                else fallback_sample
            )
        except (TypeError, ValueError):
            hist_n = int(fallback_sample or 0)
        hist_n = max(0, hist_n)
        if insufficient:
            adjustment = 0.0
        adjustment = max(-abs(maximum_adjustment), min(abs(maximum_adjustment), adjustment))
        adjustment = round(float(adjustment), 2)
        return AiOutcomeLearningResult(
            adjustment=adjustment,
            confidence=round(confidence, 4),
            reason=reason[:500],
            matched_patterns=patterns,
            supporting_patterns=patterns,
            historical_sample_size=hist_n,
            insufficient_evidence=insufficient or abs(adjustment) < 1e-9 and confidence < 0.15,
            model=model,
            prompt_version=PROMPT_VERSION,
            ok=True,
            error=None,
            openai_called=True,
        )
