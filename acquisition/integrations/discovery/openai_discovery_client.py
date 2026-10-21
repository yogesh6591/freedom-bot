"""OpenAI Responses API client for agentic discovery (web_search + Structured Outputs).

Credentials never appear in prompts, logs, DB records, or frontend payloads.
Model and reasoning effort come from Settings / environment only.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from acquisition.config import Settings, get_settings
from acquisition.services.acquisition_pricing import AcquisitionPricingService
from acquisition.services.agentic_discovery_json_schemas import (
    master_discovery_text_format,
    outreach_slot_text_format,
)

logger = logging.getLogger(__name__)

RESPONSES_URL = "https://api.openai.com/v1/responses"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class DiscoveryModelResult:
    data: dict[str, Any] | None
    text: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    search_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    # None when pricing cannot be computed (e.g. unknown long-context rate). Never invent $0.
    cost_usd: float | None = None
    input_cost_usd: float | None = None
    output_cost_usd: float | None = None
    web_search_cost_usd: float | None = None
    pricing_status: str = "ok"
    cost_complete: bool = True
    error: str | None = None
    model: str = ""
    reasoning_effort: str = ""
    response_id: str | None = None
    http_status: int | None = None
    call_type: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class OpenAIDiscoveryClient:
    """Single responsibility: call Responses API with agentic web_search + json_schema."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        reasoning_effort: str,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.reasoning_effort = (reasoning_effort or "medium").strip() or "medium"
        self.settings = settings or get_settings()
        self._transport = transport
        self.pricing = AcquisitionPricingService(self.settings)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def status_message(self) -> str:
        if not self.api_key:
            return "OpenAI API key not configured"
        if not self.model:
            return "OPENAI_DISCOVERY_MODEL not configured"
        return f"OpenAI agentic web search ({self.model})"

    def discover(
        self,
        *,
        instructions: str,
        prompt: str,
        call_type: str = "master_discovery",
        text_format: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
    ) -> DiscoveryModelResult:
        if not self.configured:
            return DiscoveryModelResult(
                data=None,
                error="openai_discovery_not_configured",
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                call_type=call_type,
            )
        if text_format is None:
            if call_type in {"outreach_slot", "slot_retry", "replacement_slot"}:
                text_format = outreach_slot_text_format()
            else:
                text_format = master_discovery_text_format()

        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": prompt,
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "text": text_format,
        }
        cache_key = (
            prompt_cache_key
            or getattr(self.settings, "discovery_prompt_cache_key", None)
            or "freedombot-acquisition-v1"
        )
        if cache_key:
            body["prompt_cache_key"] = str(cache_key)
        effort = self.reasoning_effort.lower()
        if effort and effort not in {"none", "off", "disabled"}:
            body["reasoning"] = {"effort": effort}
        answer = self._post(body, call_type=call_type)
        if answer.error and answer.error.startswith("http_400"):
            # Some accounts/models reject reasoning/include/cache — retry with schema kept.
            body.pop("reasoning", None)
            body.pop("include", None)
            body.pop("prompt_cache_key", None)
            answer = self._post(body, call_type=call_type)
        answer.model = self.model
        answer.reasoning_effort = self.reasoning_effort
        answer.call_type = call_type
        return answer

    def _post(self, body: dict[str, Any], *, call_type: str) -> DiscoveryModelResult:
        timeout = float(getattr(self.settings, "openai_discovery_timeout_seconds", 300) or 300)
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
            logger.warning("openai discovery request failed: %s", exc.__class__.__name__)
            return DiscoveryModelResult(
                data=None,
                text="",
                error=f"openai_error:{exc.__class__.__name__}",
                call_type=call_type,
            )

        if resp.status_code >= 400:
            # Never log response body (may contain sensitive prompt echoes).
            return DiscoveryModelResult(
                data=None,
                text="",
                error=f"http_{resp.status_code}",
                http_status=resp.status_code,
                call_type=call_type,
            )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            return DiscoveryModelResult(
                data=None,
                text="",
                error="openai_invalid_json_response",
                http_status=resp.status_code,
                call_type=call_type,
            )
        return self._parse(payload, call_type=call_type, http_status=resp.status_code)

    def _count_billed_web_searches(self, payload: dict[str, Any]) -> int:
        """Count billed web_search SEARCH actions (not open_page / find_in_page)."""
        count = 0
        for item in payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "web_search_call":
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            action_type = str(action.get("type") or "search").lower()
            # Default web_search_call without explicit type is a search.
            if action_type in {"", "search", "web_search"}:
                count += 1
            # Explicit non-search actions are not billed as search calls.
        return count

    def _parse(
        self,
        payload: dict[str, Any],
        *,
        call_type: str,
        http_status: int | None = None,
    ) -> DiscoveryModelResult:
        text_parts: list[str] = []
        sources: list[dict[str, str]] = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            typ = item.get("type")
            if typ == "web_search_call":
                action = item.get("action") or {}
                for src in action.get("sources") or []:
                    if isinstance(src, dict) and src.get("url"):
                        sources.append({"url": str(src["url"]), "title": str(src.get("title") or "")})
            elif typ == "message":
                for block in item.get("content") or []:
                    if isinstance(block, dict) and block.get("type") in {
                        "output_text",
                        "text",
                        "output_json",
                    }:
                        if block.get("type") == "output_json" and isinstance(block.get("json"), dict):
                            text_parts.append(json.dumps(block["json"]))
                        else:
                            text_parts.append(str(block.get("text") or ""))
        text = "\n".join(text_parts).strip()
        data = None
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                match = _JSON_OBJECT.search(text)
                if match:
                    try:
                        data = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        data = None

        usage_raw = payload.get("usage") or {}
        input_tokens = int(usage_raw.get("input_tokens") or 0)
        output_tokens = int(usage_raw.get("output_tokens") or 0)
        total_tokens = int(usage_raw.get("total_tokens") or (input_tokens + output_tokens))
        in_details = usage_raw.get("input_tokens_details") or {}
        out_details = usage_raw.get("output_tokens_details") or {}
        cached = int(in_details.get("cached_tokens") or 0)
        cache_write = int(in_details.get("cache_write_tokens") or 0)
        reasoning = int(out_details.get("reasoning_tokens") or 0)
        search_calls = self._count_billed_web_searches(payload)

        costs = self.pricing.compute_costs(
            input_tokens=input_tokens,
            cached_tokens=cached,
            cache_write_tokens=cache_write,
            output_tokens=output_tokens,
            web_search_calls=search_calls,
            model=self.model,
            reasoning_tokens=reasoning,
        )
        total_cost = costs.get("total_cost_usd")
        input_cost = costs.get("input_cost_usd")
        output_cost = costs.get("output_cost_usd")
        web_cost = costs.get("web_search_cost_usd")
        pricing_status = str(costs.get("pricing_status") or "ok")
        cost_complete = bool(costs.get("estimated_cost_available")) and total_cost is not None
        if pricing_status == "unknown_long_context_rate":
            cost_complete = False
            total_cost = None
            input_cost = None
            output_cost = None
            web_cost = None
        usage = {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached,
            "cache_write_tokens": cache_write,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning,
            "total_tokens": total_tokens,
            "web_search_calls": search_calls,
            "pricing_status": pricing_status,
            "cost_complete": cost_complete,
            "estimated_cost_available": cost_complete,
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "web_search_cost_usd": web_cost,
            "total_cost_usd": total_cost,
            "ordinary_input_tokens": costs.get("ordinary_input_tokens"),
            "message": costs.get("message"),
        }
        err = None
        if data is None:
            err = "openai_discovery_malformed_output"
        elif not isinstance(data, dict):
            err = "openai_discovery_malformed_output"
            data = None
        return DiscoveryModelResult(
            data=data if isinstance(data, dict) else None,
            text=text,
            sources=sources,
            search_calls=search_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            cache_write_tokens=cache_write,
            reasoning_tokens=reasoning,
            total_tokens=total_tokens,
            cost_usd=float(total_cost) if cost_complete and total_cost is not None else None,
            input_cost_usd=float(input_cost) if cost_complete and input_cost is not None else None,
            output_cost_usd=float(output_cost) if cost_complete and output_cost is not None else None,
            web_search_cost_usd=float(web_cost) if cost_complete and web_cost is not None else None,
            pricing_status=pricing_status,
            cost_complete=cost_complete,
            error=err,
            response_id=str(payload.get("id") or "") or None,
            http_status=http_status,
            call_type=call_type,
            usage=usage,
        )


def resolve_openai_discovery_client(db, settings: Settings | None = None) -> OpenAIDiscoveryClient:
    cfg = settings or get_settings()
    api_key: str | None = None
    if db is not None:
        try:
            from acquisition.services.openai_service import OpenAIService

            api_key, _ = OpenAIService(db=db, settings=cfg).resolve_credentials()
        except Exception:  # noqa: BLE001
            api_key = None
    if not api_key:
        api_key = (cfg.openai_api_key or "").strip() or None
    model = (cfg.openai_discovery_model or "").strip()
    effort = (cfg.openai_discovery_reasoning_effort or "medium").strip()
    return OpenAIDiscoveryClient(
        api_key=api_key,
        model=model,
        reasoning_effort=effort,
        settings=cfg,
    )
