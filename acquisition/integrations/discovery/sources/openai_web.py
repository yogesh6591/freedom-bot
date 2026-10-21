"""OpenAI web search — long-tail company discovery and per-company public research.

Uses the Responses API ``web_search`` tool. The model *suggests*; code decides:
every returned company must carry a source URL, pass directory/listicle filters,
dedupe by root domain, and (when enabled) pass a live website check before it
is saved. Directory/review sites are excluded at search time via
``filters.blocked_domains`` where the API accepts it.

Pricing note (Sep 2026): $10 per 1k web-search calls plus search content tokens
at model rates; one request may trigger several internal searches. Run cost is
estimated from the response's usage block and search-call count.
Docs: https://developers.openai.com/api/docs/guides/tools-web-search
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.sources.base import (
    REGION_BY_STATE,
    HarvestResult,
    SliceSpec,
    state_code,
)
from acquisition.integrations.discovery.types import DiscoveryCandidate
from acquisition.services.domain_tools import (
    blocked_domains_for_search,
    is_non_company_domain,
    looks_like_listicle,
    root_domain,
)

logger = logging.getLogger(__name__)

RESPONSES_URL = "https://api.openai.com/v1/responses"
SEARCH_CALL_COST = 0.01
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class WebAnswer:
    data: dict[str, Any] | None
    text: str
    sources: list[dict[str, str]] = field(default_factory=list)
    search_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    used_domain_filter: bool = True


class OpenAIWebClient:
    """Minimal Responses API client (httpx, so tests can inject a transport)."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model
        self.settings = settings or get_settings()
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def ask(self, *, instructions: str, prompt: str, context_size: str = "medium") -> WebAnswer:
        if not self.configured:
            return WebAnswer(data=None, text="", error="openai_not_configured")
        tool: dict[str, Any] = {
            "type": "web_search",
            "search_context_size": context_size,
            "user_location": {"type": "approximate", "country": "US"},
            "filters": {"blocked_domains": blocked_domains_for_search(100)},
        }
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "tools": [tool],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
        }
        answer = self._post(body)
        if answer.error and answer.error.startswith("http_400"):
            # Older models/accounts may reject filters or include — retry plainly.
            tool.pop("filters", None)
            body.pop("include", None)
            answer = self._post(body)
            answer.used_domain_filter = False
        return answer

    def _post(self, body: dict[str, Any]) -> WebAnswer:
        timeout = float(getattr(self.settings, "openai_web_timeout_seconds", 90) or 90)
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client:
                resp = client.post(
                    RESPONSES_URL,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            return WebAnswer(data=None, text="", error=f"openai_error:{exc.__class__.__name__}")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str((resp.json().get("error") or {}).get("message") or "")[:200]
            except Exception:  # noqa: BLE001
                pass
            return WebAnswer(data=None, text="", error=f"http_{resp.status_code}:{detail}")
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            return WebAnswer(data=None, text="", error="openai_bad_json")
        return self._parse(payload)

    def _parse(self, payload: dict[str, Any]) -> WebAnswer:
        texts: list[str] = []
        sources: list[dict[str, str]] = []
        seen: set[str] = set()
        calls = 0
        for item in payload.get("output") or []:
            kind = item.get("type")
            if kind == "web_search_call":
                calls += 1
                for src in ((item.get("action") or {}).get("sources") or []):
                    url = str(src.get("url") or "").strip()
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({"url": url, "title": str(src.get("title") or "")})
            elif kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") in {"output_text", "text"}:
                        texts.append(str(part.get("text") or ""))
                        for ann in part.get("annotations") or []:
                            url = str(ann.get("url") or "").strip()
                            if url and url not in seen:
                                seen.add(url)
                                sources.append({"url": url, "title": str(ann.get("title") or "")})
        text = "\n".join(t for t in texts if t).strip()
        usage = payload.get("usage") or {}
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        price_in = float(getattr(self.settings, "openai_price_input_per_m", 0.15) or 0.15)
        price_out = float(getattr(self.settings, "openai_price_output_per_m", 0.60) or 0.60)
        cost = calls * SEARCH_CALL_COST + in_tok / 1e6 * price_in + out_tok / 1e6 * price_out
        return WebAnswer(
            data=parse_json_object(text),
            text=text,
            sources=sources,
            search_calls=calls,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 5),
        )


def parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    match = _JSON_OBJECT.search(raw)
    for candidate in (raw, match.group(0) if match else None):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


_DISCOVERY_INSTRUCTIONS = """You find real, currently operating US companies for a B2B prospect list.
Rules:
- Use web search. Only return companies you found on the web in this session.
- Return the company's OWN website domain (never a directory, review site, social profile, or news article).
- Each company needs a source_url where you saw it.
- Prefer established firms with roughly 20-250 employees (about $5M-$50M revenue). Skip solo practitioners and huge enterprises.
- Never return companies in the excluded list or whose domains are in do_not_return.
- If unsure a company fits, leave it out. Fewer, correct results beat many guesses.
Return ONLY JSON: {"companies":[{"name":"","website":"","city":"","state":"","business_type":"","employee_estimate":null,"why_fit":"","source_url":""}]}"""


class OpenAIWebHarvester:
    name = "openai_web"
    label = "OpenAI web search"

    def __init__(self, client: OpenAIWebClient) -> None:
        self.client = client

    def configured(self) -> bool:
        return self.client.configured

    def status_message(self) -> str:
        return (
            f"Ready ({self.client.model})."
            if self.configured()
            else "Connect OpenAI under Integrations to enable."
        )

    def supports(self, category: str) -> bool:
        return True

    def build_prompt(self, spec: SliceSpec, *, limit: int, excluded: list[str]) -> str:
        angle = f" Focus: {spec.angle}." if spec.angle else ""
        return (
            f"Find up to {limit} {spec.category} firms headquartered in {spec.place}.{angle}\n"
            f"excluded_business_types: {', '.join(excluded) or 'none'}\n"
            f"do_not_return: {', '.join(spec.exclude_domains[:60]) or 'none'}"
        )

    def harvest(self, spec: SliceSpec, *, limit: int, excluded: list[str] | None = None) -> HarvestResult:
        prompt = self.build_prompt(spec, limit=limit, excluded=excluded or [])
        answer = self.client.ask(instructions=_DISCOVERY_INSTRUCTIONS, prompt=prompt)
        request = {
            "model": self.client.model,
            "prompt": prompt,
            "search_calls": answer.search_calls,
            "domain_filter": answer.used_domain_filter,
        }
        if answer.error:
            return HarvestResult(error=answer.error, request=request, cost_usd=answer.cost_usd)
        rows = (answer.data or {}).get("companies") or []
        cited = {root_domain(s.get("url")) for s in answer.sources if s.get("url")}
        out: list[DiscoveryCandidate] = []
        for row in rows if isinstance(rows, list) else []:
            cand = self._candidate(row, spec=spec, cited=cited)
            if cand is not None:
                out.append(cand)
            if len(out) >= limit:
                break
        return HarvestResult(
            candidates=out,
            raw_count=len(rows) if isinstance(rows, list) else 0,
            cost_usd=answer.cost_usd,
            request=request,
            exhausted=not rows,
        )

    def _candidate(
        self, row: Any, *, spec: SliceSpec, cited: set[str | None]
    ) -> DiscoveryCandidate | None:
        if not isinstance(row, dict):
            return None
        name = str(row.get("name") or "").strip()
        website = str(row.get("website") or "").strip()
        domain = root_domain(website)
        source_url = str(row.get("source_url") or "").strip() or None
        if not name or not domain or looks_like_listicle(name):
            return None
        if is_non_company_domain(domain):
            return None
        if not source_url and domain not in cited:
            return None  # no evidence it came from the web this session
        state = state_code(row.get("state")) or spec.state
        emp = row.get("employee_estimate")
        try:
            emp_int = int(emp) if emp not in (None, "") else None
        except (TypeError, ValueError):
            emp_int = None
        return DiscoveryCandidate(
            company=name,
            website=f"https://{domain}",
            domain=domain,
            city=(str(row.get("city") or "").strip() or None),
            state=state,
            region=REGION_BY_STATE.get(state or ""),
            industry=str(row.get("business_type") or spec.category).strip()[:120] or spec.category,
            source="autonomous_discovery",
            source_url=source_url or f"https://{domain}",
            source_type="web_search",
            discovered_at=datetime.now(timezone.utc),
            notes=f"OpenAI web search suggestion: {str(row.get('why_fit') or '').strip()[:300]}",
            identity_confidence=0.6,
            identity_uncertain=False,
            raw={"why_fit": row.get("why_fit")},
            discovery_source=self.name,
            slice_key=spec.key,
            description=str(row.get("why_fit") or "").strip()[:600] or None,
            headcount_hint=emp_int,
        )
