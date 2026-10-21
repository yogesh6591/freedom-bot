"""AI semantic validation for live public research (GPT-4o-mini).

Hard domain/company rules run FIRST. This layer extracts structured facts only
from sources that already passed hard entity grounding. AI never overrides a
hard rejection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.services.assessment_extraction import parse_extractor_json
from acquisition.services.company_identity import domain_from_url, filter_sources_for_ai_validation
from acquisition.services.openai_service import OpenAIService
from acquisition.services.prospect_identity import normalize_domain

logger = logging.getLogger(__name__)

RELEVANCE_SAME_COMPANY = "relevant_same_company"
RELEVANCE_EXTERNAL_VERIFIED = "relevant_external_verified"
# Backward-compatible alias used by older tests / payloads.
RELEVANCE_EXTERNAL_NEWS = "relevant_external_news"
RELEVANCE_IRRELEVANT = "unrelated"
RELEVANCE_IRRELEVANT_LEGACY = "irrelevant"
RELEVANCE_UNCERTAIN = "uncertain"

_HQ_LOCATION_TYPES = frozenset({"headquarters", "hq", "primary"})
_NON_PRIMARY_LOCATION_TYPES = frozenset(
    {"office", "expansion", "acquisition", "event", "unknown"}
)

_SYSTEM_PROMPT = """You are a strict business-entity research validator.

Your job is NOT to guess.

You may only accept a fact when the supplied source clearly supports
that fact for the exact candidate company.

The candidate company's canonical name and domain are authoritative
identity anchors.

Never treat acronym similarity, generic industry language, similar
service descriptions, or title similarity as proof that two
organizations are the same.

For external sources, require explicit evidence that the source refers
to the candidate company.

If the source refers to another organization, return it as unrelated.

If you are uncertain whether the source belongs to the candidate
company, return uncertain and do not extract company facts from it.

Never invent:
- company identity
- decision-maker
- title
- email
- location
- revenue
- headcount
- signals

Decision-maker rules:
A person may be accepted only if the source clearly associates that
person and title with the exact candidate company.

Do not infer a person from:
- acronym-only references
- generic leadership text
- role-only text
- marketing phrases
- unrelated organizations with similar initials

Location rules:
Distinguish between:
- headquarters / primary company location
- office/location
- acquisition target location
- expansion location
- event/news location

Do not set the prospect's main company location from an acquisition,
expansion, event, or article location unless the source explicitly says
it is the company's headquarters or primary location.

Signals:
A signal may only be attached if it clearly applies to the candidate
company.

If evidence is weak or ambiguous:
return null/uncertain rather than guessing.

Prefer missing data over incorrect data.

Return ONLY a JSON object. No chain-of-thought. No markdown.
"""


@dataclass(slots=True)
class AISourceVerdict:
    source_url: str | None
    relevance: str
    confidence: float
    reason: str | None = None


@dataclass(slots=True)
class AISignalVerdict:
    type: str
    summary: str
    confidence: float
    source_url: str | None = None


@dataclass(slots=True)
class AIResearchValidationResult:
    available: bool
    same_company: bool | None = None
    company_name: str | None = None
    company_name_confidence: float = 0.0
    company_name_source_url: str | None = None
    decision_maker_name: str | None = None
    decision_maker_title: str | None = None
    decision_maker_confidence: float = 0.0
    decision_maker_source_url: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_confidence: float = 0.0
    contact_source_url: str | None = None
    city: str | None = None
    state: str | None = None
    region: str | None = None
    location_type: str | None = None
    location_confidence: float = 0.0
    location_source_url: str | None = None
    industry: str | None = None
    industry_confidence: float = 0.0
    signals: list[AISignalVerdict] = field(default_factory=list)
    sources: list[AISourceVerdict] = field(default_factory=list)
    reason: str | None = None
    error: str | None = None
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    sources_sent: int = 0
    sources_hard_rejected: int = 0


class AIResearchValidationService:
    """One bounded GPT-4o-mini call per prospect research batch."""

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

    def validate_batch(
        self,
        *,
        company: str,
        domain: str | None = None,
        website: str | None = None,
        source_url: str | None = None,
        industry: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> AIResearchValidationResult:
        if not bool(getattr(self.settings, "research_validation_enabled", True)):
            return AIResearchValidationResult(
                available=False,
                error="research_validation_disabled",
                reason="AI research validation disabled by config.",
            )

        raw_sources = list(sources or [])
        # Hard entity filter FIRST — AI never sees obviously unrelated domains.
        grounded = filter_sources_for_ai_validation(
            raw_sources, company_name=company, domain=domain
        )
        deduped = self._dedupe_sources(grounded)
        hard_rejected = max(0, len(self._dedupe_sources(raw_sources)) - len(deduped))

        if not deduped:
            return AIResearchValidationResult(
                available=False,
                error="no_grounded_sources",
                reason=(
                    "No hard-grounded sources remain for AI validation; "
                    "using deterministic company-bound rules only."
                ),
                sources_sent=0,
                sources_hard_rejected=hard_rejected,
            )

        model = (
            (getattr(self.settings, "research_validation_model", None) or "").strip()
            or (self.settings.openai_model or "").strip()
            or "gpt-4o-mini"
        )

        payload = {
            "candidate": {
                "company_name": company,
                "domain": domain,
                "website": website,
                "source_url": source_url,
                "industry": industry,
            },
            "existing": existing or {},
            "sources": deduped,
            "note": (
                "These sources already passed hard company/domain grounding. "
                "Still reject any source that does not clearly refer to the "
                "exact candidate company. Do not invent facts."
            ),
            "required_json_schema": {
                "same_company": "boolean",
                "company_name": {
                    "value": "string|null",
                    "confidence": "0..1",
                    "supporting_source_url": "string|null",
                },
                "decision_maker": {
                    "name": "string|null",
                    "title": "string|null",
                    "confidence": "0..1",
                    "supporting_source_url": "string|null",
                },
                "contact": {
                    "email": "string|null",
                    "phone": "string|null",
                    "confidence": "0..1",
                    "supporting_source_url": "string|null",
                },
                "location": {
                    "city": "string|null",
                    "state": "string|null",
                    "region": "string|null",
                    "location_type": (
                        "headquarters|office|expansion|acquisition|event|unknown"
                    ),
                    "confidence": "0..1",
                    "supporting_source_url": "string|null",
                },
                "industry": "string|null",
                "industry_confidence": "0..1",
                "signals": [
                    {
                        "type": (
                            "hiring|technology|expansion|leadership|"
                            "acquisition|new_offering|other"
                        ),
                        "summary": "string",
                        "confidence": "0..1",
                        "source_url": "string|null",
                    }
                ],
                "sources": [
                    {
                        "source_url": "string",
                        "relevance": (
                            "relevant_same_company|relevant_external_verified|"
                            "unrelated|uncertain"
                        ),
                        "confidence": "0..1",
                        "reason": "short string",
                    }
                ],
                "reason": "short factual reason",
            },
        }

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extract only grounded facts for the candidate company from "
                    "these hard-filtered sources. Return the required JSON only.\n\n"
                    f"{json.dumps(payload, default=str)}"
                ),
            },
        ]

        try:
            raw_text = (self._openai_call or self._default_openai_call)(messages, model)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AI research validation unavailable: %s",
                exc.__class__.__name__,
            )
            return AIResearchValidationResult(
                available=False,
                error=f"openai_unavailable:{exc.__class__.__name__}",
                reason="AI validation unavailable; using deterministic rules only.",
                model=model,
                sources_sent=len(deduped),
                sources_hard_rejected=hard_rejected,
            )

        parsed = parse_extractor_json(raw_text)
        if not parsed:
            return AIResearchValidationResult(
                available=False,
                error="invalid_ai_json",
                reason="AI returned unusable JSON; using deterministic rules only.",
                model=model,
                sources_sent=len(deduped),
                sources_hard_rejected=hard_rejected,
            )
        result = self._normalize(parsed, model=model)
        result.sources_sent = len(deduped)
        result.sources_hard_rejected = hard_rejected
        return result

    def _default_openai_call(
        self, messages: list[dict[str, str]], model: str
    ) -> str:
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

    def _dedupe_sources(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        max_sources = max(1, int(getattr(self.settings, "ai_research_max_sources", 12) or 12))
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for raw in sources:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            snippet = str(raw.get("snippet") or raw.get("note") or "").strip()
            url = str(raw.get("url") or raw.get("source_url") or raw.get("link") or "").strip()
            if not title and not snippet and not url:
                continue
            key = (url or f"{title}|{snippet[:80]}").lower()
            if key in seen:
                continue
            seen.add(key)
            host = str(raw.get("domain") or "").strip() or domain_from_url(url)
            out.append(
                {
                    "title": title[:200],
                    "snippet": snippet[:400],
                    "url": url or None,
                    "domain": host,
                    "source_type": str(raw.get("source_type") or "search"),
                    "hard_relevance": raw.get("hard_relevance"),
                }
            )
            if len(out) >= max_sources:
                break
        return out

    def _normalize(
        self, data: dict[str, Any], *, model: str
    ) -> AIResearchValidationResult:
        dm = data.get("decision_maker") if isinstance(data.get("decision_maker"), dict) else {}
        contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        company_obj = data.get("company_name")
        if isinstance(company_obj, dict):
            company_name = str(company_obj.get("value") or "").strip() or None
            company_conf = _clip_conf(company_obj.get("confidence"))
            company_src = str(company_obj.get("supporting_source_url") or "").strip() or None
        else:
            company_name = str(company_obj or "").strip() or None
            company_conf = _clip_conf(data.get("company_name_confidence"))
            company_src = str(data.get("company_name_source_url") or "").strip() or None

        signals: list[AISignalVerdict] = []
        for item in data.get("signals") or []:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            signals.append(
                AISignalVerdict(
                    type=str(item.get("type") or "other").strip().lower() or "other",
                    summary=summary[:280],
                    confidence=_clip_conf(item.get("confidence")),
                    source_url=(str(item.get("source_url") or "").strip() or None),
                )
            )

        sources: list[AISourceVerdict] = []
        for item in data.get("sources") or []:
            if not isinstance(item, dict):
                continue
            relevance = _normalize_relevance(
                str(item.get("relevance") or RELEVANCE_UNCERTAIN)
            )
            sources.append(
                AISourceVerdict(
                    source_url=(str(item.get("source_url") or "").strip() or None),
                    relevance=relevance,
                    confidence=_clip_conf(item.get("confidence")),
                    reason=(str(item.get("reason") or "").strip() or None),
                )
            )

        same = data.get("same_company")
        same_company = same if isinstance(same, bool) else None
        loc_type = str(location.get("location_type") or "").strip().lower() or None

        return AIResearchValidationResult(
            available=True,
            same_company=same_company,
            company_name=company_name,
            company_name_confidence=company_conf,
            company_name_source_url=company_src,
            decision_maker_name=(str(dm.get("name") or "").strip() or None),
            decision_maker_title=(str(dm.get("title") or "").strip() or None),
            decision_maker_confidence=_clip_conf(dm.get("confidence")),
            decision_maker_source_url=(
                str(dm.get("supporting_source_url") or "").strip() or None
            ),
            contact_email=(str(contact.get("email") or "").strip() or None),
            contact_phone=(str(contact.get("phone") or "").strip() or None),
            contact_confidence=_clip_conf(contact.get("confidence")),
            contact_source_url=(
                str(contact.get("supporting_source_url") or "").strip() or None
            ),
            city=(str(location.get("city") or "").strip() or None),
            state=(str(location.get("state") or "").strip() or None),
            region=(str(location.get("region") or "").strip() or None),
            location_type=loc_type,
            location_confidence=_clip_conf(location.get("confidence")),
            location_source_url=(
                str(location.get("supporting_source_url") or "").strip() or None
            ),
            industry=(str(data.get("industry") or "").strip() or None),
            industry_confidence=_clip_conf(
                data.get("industry_confidence") or company_conf
            ),
            signals=signals,
            sources=sources,
            reason=(str(data.get("reason") or "").strip() or None),
            model=model,
            raw=data,
        )


def is_headquarters_location(location_type: str | None) -> bool:
    return (location_type or "").strip().lower() in _HQ_LOCATION_TYPES


def is_non_primary_location(location_type: str | None) -> bool:
    return (location_type or "").strip().lower() in _NON_PRIMARY_LOCATION_TYPES


def _normalize_relevance(value: str) -> str:
    text = (value or "").strip().lower()
    mapping = {
        RELEVANCE_SAME_COMPANY: RELEVANCE_SAME_COMPANY,
        RELEVANCE_EXTERNAL_VERIFIED: RELEVANCE_EXTERNAL_VERIFIED,
        RELEVANCE_EXTERNAL_NEWS: RELEVANCE_EXTERNAL_VERIFIED,
        "relevant_external_news": RELEVANCE_EXTERNAL_VERIFIED,
        RELEVANCE_IRRELEVANT: RELEVANCE_IRRELEVANT,
        RELEVANCE_IRRELEVANT_LEGACY: RELEVANCE_IRRELEVANT,
        "irrelevant": RELEVANCE_IRRELEVANT,
        RELEVANCE_UNCERTAIN: RELEVANCE_UNCERTAIN,
    }
    return mapping.get(text, RELEVANCE_UNCERTAIN)


def _clip_conf(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, num))


def source_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return normalize_domain(host) or host or None
    except Exception:  # noqa: BLE001
        return None
