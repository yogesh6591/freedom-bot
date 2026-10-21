"""Exa company search harvester (LEGACY — disabled in the active discovery path).

Kept for rollback / future testing when DISCOVERY_ENABLE_LEGACY_SOURCES=true
and DISCOVERY_PROVIDER=multi_legacy. The agentic OpenAI path never imports this.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.sources.base import HarvestResult, SliceSpec, state_code
from acquisition.integrations.discovery.types import DiscoveryCandidate
from acquisition.services.domain_tools import is_non_company_domain, root_domain

logger = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"


class ExaCompanyHarvester:
    name = "exa"
    label = "Exa company search"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self.api_key = (api_key if api_key is not None else (self.settings.exa_api_key or "")).strip()

    def configured(self) -> bool:
        return bool(self.api_key)

    def status_message(self) -> str:
        if not self.api_key:
            return "Exa API key not configured (legacy; disabled in active discovery)"
        return "Exa company index (legacy; disabled in active discovery)"

    def supports(self, category: str) -> bool:
        return True

    def harvest(self, spec: SliceSpec, *, limit: int = 40) -> HarvestResult:
        if not self.configured():
            return HarvestResult(error="exa_not_configured", request={})
        query = f"{spec.category} headquartered in {spec.place}"
        body = {
            "query": query,
            "type": "auto",
            "numResults": max(1, min(int(limit), 100)),
            "contents": {"text": False},
        }
        request = {"query": query, "numResults": body["numResults"]}
        try:
            with httpx.Client(timeout=30.0, transport=self._transport) as client:
                resp = client.post(
                    EXA_SEARCH_URL,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "x-api-key": self.api_key,
                    },
                )
                if resp.status_code >= 400:
                    return HarvestResult(
                        error=f"exa_http_{resp.status_code}",
                        request=request,
                        raw_count=0,
                    )
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("exa harvest failed: %s", exc)
            return HarvestResult(error=f"exa_error:{exc.__class__.__name__}", request=request)

        cost = 0.0
        try:
            cost = float((payload.get("costDollars") or {}).get("total") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0

        candidates: list[DiscoveryCandidate] = []
        for row in payload.get("results") or []:
            cand = self._to_candidate(row, spec=spec)
            if cand is None:
                continue
            candidates.append(cand)
            if len(candidates) >= limit:
                break

        return HarvestResult(
            candidates=candidates[:limit],
            raw_count=len(payload.get("results") or []),
            exhausted=True,
            cost_usd=cost,
            request=request,
        )

    def _to_candidate(self, row: dict[str, Any], *, spec: SliceSpec) -> DiscoveryCandidate | None:
        url = (row.get("url") or "").strip()
        domain = root_domain(url) if url else None
        if domain and is_non_company_domain(domain):
            return None
        entities = row.get("entities") or []
        company_ent = next((e for e in entities if (e.get("type") or "").lower() == "company"), None)
        props = (company_ent or {}).get("properties") or {}
        name = (props.get("name") or row.get("title") or "").strip()
        if not name:
            return None
        hq = props.get("headquarters") or {}
        country = (hq.get("country") or "").strip().lower()
        if country and "united states" not in country and country not in {"us", "usa", "u.s.", "u.s.a."}:
            return None
        state = state_code(hq.get("state")) or state_code(spec.state)
        city = (hq.get("city") or "").strip() or None
        workforce = props.get("workforce") or {}
        headcount = workforce.get("total")
        try:
            headcount_hint = int(headcount) if headcount is not None else None
        except (TypeError, ValueError):
            headcount_hint = None
        return DiscoveryCandidate(
            company=name,
            website=url or (f"https://{domain}" if domain else None),
            domain=domain,
            city=city,
            state=state,
            industry=spec.category,
            description=(props.get("description") or "").strip() or None,
            source="autonomous_discovery",
            source_url=url or None,
            source_type="company_index",
            discovery_source="exa",
            slice_key=spec.key,
            headcount_hint=headcount_hint,
            raw={"exa": row},
        )
