"""AI-assisted discovery search-query planning from the full ICP.

AI proposes bounded search queries to find COMPANIES matching the ICP.
Public search + research + validation establish facts — the LLM does not.
Falls back to deterministic template queries when OpenAI is unavailable.
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
from acquisition.services import acquisition_trace as acq_trace

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
PROMPT_VERSION = "discovery_query_plan_v1"
MAX_QUERIES = 8

_SYSTEM_PROMPT = """You plan public web search queries to find real COMPANIES that match an ICP.

Rules:
- Return ONLY valid JSON.
- Queries must target companies/organizations (firms, practices, agencies, consultancies),
  not job seekers, directories, social posts, or generic service landing pages.
- Prefer queries that surface About/Team/Company pages over service-offering keyword pages.
- Respect excluded business types: do not craft queries that deliberately seek excluded industries.
- Use geography, revenue band, buyer titles, fit themes, and priority categories from the ICP.
- Keep each query concise and suitable for a web search API.
- Do not invent ICP facts that are not in the payload.
- Produce between 3 and 8 queries.
"""


@dataclass(slots=True)
class DiscoveryQueryPlan:
    queries: list[str] = field(default_factory=list)
    source: str = "deterministic"
    model: str | None = None
    ok: bool = True
    error: str | None = None


def build_deterministic_discovery_queries(icp: dict[str, Any]) -> list[str]:
    """Safe fallback query set derived from ICP priority categories + geography."""
    categories = [str(x).strip() for x in (icp.get("priority_categories") or []) if str(x).strip()]
    if not categories:
        categories = ["advisory", "consultants", "agencies"]
    excluded = [str(x).strip().lower() for x in (icp.get("excluded_categories") or []) if str(x).strip()]
    # Drop priority categories that literally match an exclusion (avoid targeting them).
    filtered = [c for c in categories if c.lower() not in excluded]
    if not filtered:
        filtered = categories
    geo = icp.get("geography") or {}
    geo_terms = ["United States"]
    for state in (geo.get("states") or [])[:3]:
        if state:
            geo_terms.append(str(state))
    fit_themes = [str(x).strip() for x in (icp.get("fit_themes") or []) if str(x).strip()]
    buyer = [str(x).strip() for x in (icp.get("buyer_titles") or []) if str(x).strip()]
    queries: list[str] = []
    for cat in filtered[:4]:
        for geo_term in geo_terms[:2]:
            queries.append(
                f'{cat} company {geo_term} ("about us" OR "our team" OR "founded")'
            )
            queries.append(f'{cat} firm {geo_term} hiring OR careers OR "open roles"')
    if fit_themes:
        theme = fit_themes[0]
        queries.append(
            f'{filtered[0]} "{theme}" company United States ("about us" OR leadership)'
        )
    if buyer:
        queries.append(
            f'{filtered[0]} company United States "{buyer[0]}" OR founder OR CEO'
        )
    # Exclude obvious excluded-industry targeting phrases when present.
    if excluded:
        # Soft negative keywords for common exclusions (bounded).
        negatives = " ".join(f'-"{ex}"' for ex in excluded[:4])
        queries = [f"{q} {negatives}".strip() for q in queries]
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        unique.append(q)
    return unique[:MAX_QUERIES]


class DiscoveryQueryPlanningService:
    """Generate bounded structured discovery queries from the full ICP."""

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

    def plan_queries(self, icp: dict[str, Any]) -> DiscoveryQueryPlan:
        fallback = build_deterministic_discovery_queries(icp)
        model = (
            (getattr(self.settings, "research_validation_model", None) or "").strip()
            or (self.settings.openai_model or "").strip()
            or "gpt-4o-mini"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Build a bounded search query plan for company discovery.\n"
                    "Return ONLY this JSON shape:\n"
                    '{"queries":[{"query":"string","intent":"string"}],'
                    '"notes":"string"}\n\n'
                    f"PROMPT_VERSION={PROMPT_VERSION}\n"
                    f"MAX_QUERIES={MAX_QUERIES}\n\n"
                    f"ICP_PAYLOAD:\n{json.dumps(self._icp_payload(icp), default=str)}"
                ),
            },
        ]
        try:
            raw = (self._openai_call or self._default_openai_call)(messages, model)
        except Exception as exc:  # noqa: BLE001
            logger.info("discovery query planning unavailable: %s", exc.__class__.__name__)
            return DiscoveryQueryPlan(
                queries=fallback,
                source="deterministic_fallback",
                model=model,
                ok=False,
                error=exc.__class__.__name__,
            )

        parsed = self._parse_json(raw)
        queries = self._normalize_queries(parsed)
        if not queries:
            return DiscoveryQueryPlan(
                queries=fallback,
                source="deterministic_fallback",
                model=model,
                ok=False,
                error="malformed_or_empty_queries",
            )
        return DiscoveryQueryPlan(
            queries=queries,
            source="ai",
            model=model,
            ok=True,
        )

    def _icp_payload(self, icp: dict[str, Any]) -> dict[str, Any]:
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
            "discovery_intent": (
                "Find companies that match the ICP as their primary business type, "
                "not companies that merely advertise a related service keyword."
            ),
        }

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
            temperature=0.2,
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
    def _normalize_queries(parsed: dict[str, Any] | None) -> list[str]:
        if not parsed:
            return []
        raw_list = parsed.get("queries")
        if not isinstance(raw_list, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw_list:
            if isinstance(item, str):
                q = item.strip()
            elif isinstance(item, dict):
                q = str(item.get("query") or "").strip()
            else:
                continue
            if not q or q.lower() in seen:
                continue
            seen.add(q.lower())
            out.append(q)
            if len(out) >= MAX_QUERIES:
                break
        return out


def resolve_discovery_queries(
    icp: dict[str, Any],
    *,
    db: Session | None = None,
    settings: Settings | None = None,
    planner: DiscoveryQueryPlanningService | None = None,
) -> DiscoveryQueryPlan:
    """Plan queries (AI when available) and emit ACQ query traces."""
    cfg = settings or get_settings()
    svc = planner or DiscoveryQueryPlanningService(db, settings=cfg)
    plan = svc.plan_queries(icp)
    if acq_trace.is_acquisition_trace_enabled(cfg):
        acq_trace.emit_block(
            "QUERY_PLAN",
            [
                f"Source={plan.source}",
                f"Model={plan.model or 'none'}",
                f"QueryCount={len(plan.queries)}",
                f"Ok={'true' if plan.ok else 'false'}",
                f"Error={plan.error or 'none'}",
            ],
        )
    return plan
