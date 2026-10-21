"""AI-personalized initial outreach for autonomous acquisition.

Reuses OpenAIService credentials. Deterministic grounded fallback when LLM
unavailable (tests / missing key). Never invents unsupported facts.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.prospect import Prospect
from acquisition.services.openai_service import OpenAIService
from acquisition.services.prospect_research_service import ProspectResearchService

logger = logging.getLogger(__name__)

SENDER_IDENTITY = "JeanneCAIO / FreedomBot Team"

_AI_JARGON_LEAD = re.compile(
    r"^\s*(ai\s+transformation|ai\s+agents|automation\s+platform|generative\s+ai)",
    re.I,
)
_BAD_DIAGNOSIS = re.compile(
    r"\b(clearly has|staffing inefficiency|guaranteed|roi of|private knowledge)\b",
    re.I,
)
_CALENDAR_OR_CHECKOUT = re.compile(
    r"(cal\.com|calendar\.google|stripe\.com|checkout\.|book a call|schedule a call)",
    re.I,
)


@dataclass(slots=True)
class PersonalizedOutreachResult:
    subject: str
    body: str
    message_angle: str
    supporting_signal: str | None
    supporting_source_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    sender_identity: str = SENDER_IDENTITY
    used_llm: bool = False


GenerateFn = Callable[[dict[str, Any]], dict[str, Any]]


class PersonalizedOutreachService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        generate_fn: GenerateFn | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._generate_fn = generate_fn
        self.evidence = ProspectResearchService(db)

    def generate(
        self,
        prospect: Prospect,
        *,
        assessment_url: str,
    ) -> PersonalizedOutreachResult:
        context = self._build_context(prospect, assessment_url=assessment_url)
        raw: dict[str, Any]
        used_llm = False
        if self._generate_fn is not None:
            raw = self._generate_fn(context)
            used_llm = True
        else:
            llm_raw = self._try_llm(context)
            if llm_raw is not None:
                raw = llm_raw
                used_llm = True
            else:
                raw = self._deterministic_fallback(context)

        result = self._normalize_and_validate(raw, context=context, used_llm=used_llm)
        return result

    def _build_context(self, prospect: Prospect, *, assessment_url: str) -> dict[str, Any]:
        sources = self.evidence.list_sources(prospect.id)
        # Prefer medium+ confidence signals/notes.
        usable_signals: list[str] = []
        if isinstance(prospect.public_signals, list):
            for item in prospect.public_signals:
                text = str(item).strip()
                if text and not _BAD_DIAGNOSIS.search(text):
                    usable_signals.append(text)
        source_ids = [str(s.id) for s in sources if (s.confidence or 0) >= 0.5]
        timing_sources = [s for s in sources if (s.category or "") == "timing"]
        low_conf_only = bool(timing_sources) and all(
            (s.confidence or 0) < 0.45 for s in timing_sources
        )
        signal = usable_signals[0] if usable_signals and not low_conf_only else None
        if usable_signals and low_conf_only:
            # Spec: low-confidence signal can be omitted.
            signal = None

        first_name = (prospect.executive_name or "").strip().split()[0] if prospect.executive_name else "there"
        if first_name.lower() in {"unknown", "decision-maker"}:
            first_name = "there"

        return {
            "company": prospect.company,
            "company_description": prospect.company_description,
            "decision_maker": prospect.executive_name,
            "first_name": first_name,
            "title": prospect.executive_title,
            "industry": prospect.industry,
            "revenue_band": prospect.revenue_band,
            "headcount_band": prospect.headcount_band,
            "fit_score": prospect.fit_score,
            "fit_reasons": prospect.fit_reasons or [],
            "timing_score": prospect.timing_score,
            "timing_reasons": prospect.timing_reasons or [],
            "public_signals": usable_signals,
            "selected_signal": signal,
            "research_notes": prospect.research_notes,
            "assessment_url": assessment_url,
            "sender_identity": (
                self.settings.autonomous_sender_identity or SENDER_IDENTITY
            ).strip(),
            "supporting_source_ids": source_ids[:5],
        }

    def _try_llm(self, context: dict[str, Any]) -> dict[str, Any] | None:
        openai = OpenAIService(db=self.db, settings=self.settings)
        api_key, model = openai.resolve_credentials()
        if not api_key:
            return None
        system = (
            "You write concise executive cold emails for JeanneCAIO / FreedomBot Team. "
            "Never impersonate Jeanne personally. Never claim private knowledge. "
            "Lead with business concerns (scalability, overhead, expensive human dependency, "
            "organizational complexity, fragmented knowledge, capacity, leverage) — "
            "NOT AI transformation / AI agents / automation jargon. "
            "CTA is the free assessment URL only — no calendar, no checkout. "
            "Public signals are hypotheses, not diagnoses. "
            "Return JSON only with keys: subject, body, message_angle, supporting_signal, confidence."
        )
        user = (
            "Prospect context (use only what is supported):\n"
            f"{json.dumps(context, default=str)[:6000]}\n\n"
            "Write the initial outreach email body WITHOUT the assessment URL line "
            "(it will be appended). Sign as the sender_identity. "
            "If selected_signal is null, use a safe broader ICP-based angle."
        )
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            response = client.responses.create(
                model=model or "gpt-4o-mini",
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = (getattr(response, "output_text", None) or "").strip()
            if not text:
                return None
            # Extract JSON object if wrapped in markdown.
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return None
            data = json.loads(match.group(0))
            if not isinstance(data, dict):
                return None
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("personalized outreach LLM failed: %s", type(exc).__name__)
            return None

    def _deterministic_fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        company = context["company"]
        first = context["first_name"] or "there"
        signal = context.get("selected_signal")
        industry = context.get("industry") or "expertise-driven team"
        if signal:
            angle = "public_signal_capacity"
            opener = (
                f"I noticed a public signal that may relate to capacity and leverage at {company}: "
                f"{signal.rstrip('.')}."
            )
            confidence = 0.65
        else:
            angle = "icp_operational_complexity"
            opener = (
                f"I work with {industry} organizations where expensive human dependency and "
                f"organizational complexity often slow growth more than strategy does."
            )
            confidence = 0.45
            signal = None
        subject = f"Operational bandwidth at {company}"
        body = (
            f"Hi {first},\n\n"
            f"{opener}\n\n"
            f"When teams scale, overhead and fragmented knowledge usually show up before "
            f"anyone has time to redesign how work gets done.\n\n"
            f"Would you be open to a short free assessment for {company}?\n\n"
            f"Best,\n"
            f"{context['sender_identity']}"
        )
        return {
            "subject": subject,
            "body": body,
            "message_angle": angle,
            "supporting_signal": signal,
            "confidence": confidence,
        }

    def _normalize_and_validate(
        self,
        raw: dict[str, Any],
        *,
        context: dict[str, Any],
        used_llm: bool,
    ) -> PersonalizedOutreachResult:
        subject = str(raw.get("subject") or "").strip() or f"Operational bandwidth at {context['company']}"
        body = str(raw.get("body") or "").strip()
        if not body:
            body = self._deterministic_fallback(context)["body"]
        angle = str(raw.get("message_angle") or "operational_leverage").strip()
        signal = raw.get("supporting_signal")
        if signal is not None:
            signal = str(signal).strip() or None
        if signal is None:
            signal = context.get("selected_signal")
        try:
            confidence = float(raw.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        # Safety rewrites / rejects.
        if _AI_JARGON_LEAD.search(body) or _AI_JARGON_LEAD.search(subject):
            fallback = self._deterministic_fallback(context)
            subject = fallback["subject"]
            body = fallback["body"]
            angle = fallback["message_angle"]
            used_llm = False
        if _BAD_DIAGNOSIS.search(body):
            fallback = self._deterministic_fallback({**context, "selected_signal": None})
            body = fallback["body"]
            signal = None
            angle = fallback["message_angle"]
        body = _CALENDAR_OR_CHECKOUT.sub("[link omitted]", body)
        subject = _CALENDAR_OR_CHECKOUT.sub("", subject).strip()

        sender = context["sender_identity"]
        if "jeanne researched" in body.lower() or "i personally researched" in body.lower():
            body = re.sub(
                r"(?i)i personally researched[^.]*\.",
                "Our team reviews public business signals.",
                body,
            )
        if sender.lower() not in body.lower():
            body = body.rstrip() + f"\n\nBest,\n{sender}"

        # Assessment URL is appended by Instantly template ({{assessment_url}}) and
        # by activation/preview for snapshots — keep body free of calendar/checkout.
        url = context["assessment_url"]
        if url and url in body:
            # Prefer Instantly merge for CTA; strip literal URL to avoid duplicates.
            body = body.replace(url, "").strip()
        body = _CALENDAR_OR_CHECKOUT.sub("[link omitted]", body)

        return PersonalizedOutreachResult(
            subject=subject[:500],
            body=body.strip(),
            message_angle=angle,
            supporting_signal=signal,
            supporting_source_ids=list(context.get("supporting_source_ids") or []),
            confidence=confidence,
            sender_identity=sender,
            used_llm=used_llm,
        )
