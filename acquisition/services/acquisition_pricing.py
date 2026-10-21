"""Centralized Acquisition OpenAI pricing (USD).

Do not scatter pricing math in templates or call sites.
Never treat an unconfigured long-context rate as $0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from acquisition.config import Settings, get_settings

# Last known Standard GPT-6 Luna rates (Admin-visible).
PRICING_LAST_UPDATED = date(2026, 10, 1)

PRICING_OK = "ok"
PRICING_UNKNOWN_LONG_CONTEXT = "unknown_long_context_rate"
PRICING_INCOMPLETE = "incomplete_pricing_config"


@dataclass(frozen=True, slots=True)
class AcquisitionModelPricing:
    model: str
    input_per_million: float
    cached_input_per_million: float
    cache_write_per_million: float
    output_per_million: float
    web_search_per_call: float
    long_context_threshold_tokens: int | None = 128_000
    long_context_input_per_million: float | None = None
    long_context_output_per_million: float | None = None
    last_updated: date = PRICING_LAST_UPDATED

    @property
    def long_context_configured(self) -> bool:
        return (
            self.long_context_input_per_million is not None
            and float(self.long_context_input_per_million) > 0
            and self.long_context_output_per_million is not None
            and float(self.long_context_output_per_million) > 0
        )


class AcquisitionPricingService:
    """Compute token + web-search costs from usage counters."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def pricing_for_model(self, model: str | None = None) -> AcquisitionModelPricing:
        name = (model or getattr(self.settings, "openai_discovery_model", "") or "gpt-6-luna").strip()
        input_rate = float(
            getattr(self.settings, "discovery_price_input_per_m", None)
            or getattr(self.settings, "openai_price_input_per_m", 0.10)
            or 0.10
        )
        output_rate = float(
            getattr(self.settings, "discovery_price_output_per_m", None)
            or getattr(self.settings, "openai_price_output_per_m", 0.50)
            or 0.50
        )
        cached = float(getattr(self.settings, "discovery_price_cached_input_per_m", 0.01) or 0.01)
        cache_write = float(getattr(self.settings, "discovery_price_cache_write_per_m", 0.125) or 0.125)
        web = float(getattr(self.settings, "discovery_price_web_search_per_call", 0.01) or 0.01)
        threshold = int(
            getattr(self.settings, "discovery_price_long_context_threshold_tokens", 128_000) or 128_000
        )
        lc_in_raw = getattr(self.settings, "discovery_price_long_context_input_per_m", None)
        lc_out_raw = getattr(self.settings, "discovery_price_long_context_output_per_m", None)
        try:
            lc_in = float(lc_in_raw) if lc_in_raw not in (None, "") else None
        except (TypeError, ValueError):
            lc_in = None
        try:
            lc_out = float(lc_out_raw) if lc_out_raw not in (None, "") else None
        except (TypeError, ValueError):
            lc_out = None
        # Treat 0 as "not configured" — never bill long-context at $0.
        if lc_in is not None and lc_in <= 0:
            lc_in = None
        if lc_out is not None and lc_out <= 0:
            lc_out = None
        return AcquisitionModelPricing(
            model=name,
            input_per_million=input_rate,
            cached_input_per_million=cached,
            cache_write_per_million=cache_write,
            output_per_million=output_rate,
            web_search_per_call=web,
            long_context_threshold_tokens=threshold if threshold > 0 else None,
            long_context_input_per_million=lc_in,
            long_context_output_per_million=lc_out,
        )

    def validate_config(self, *, model: str | None = None) -> dict[str, Any]:
        """Local pricing/config validation (no OpenAI HTTP)."""
        rates = self.pricing_for_model(model)
        problems: list[str] = []
        if not rates.model:
            problems.append("OPENAI_DISCOVERY_MODEL empty")
        for label, value in (
            ("input_per_million", rates.input_per_million),
            ("cached_input_per_million", rates.cached_input_per_million),
            ("cache_write_per_million", rates.cache_write_per_million),
            ("output_per_million", rates.output_per_million),
            ("web_search_per_call", rates.web_search_per_call),
        ):
            if value is None or float(value) < 0:
                problems.append(f"{label} invalid")
            if float(value or 0) == 0 and label != "web_search_per_call":
                # Zero token rates would silently under-report cost.
                problems.append(f"{label} must be > 0")
        effort = (getattr(self.settings, "openai_discovery_reasoning_effort", "") or "").strip().lower()
        supported = {"", "none", "off", "disabled", "minimal", "low", "medium", "high", "xhigh"}
        if effort and effort not in supported:
            problems.append(f"unsupported reasoning effort: {effort}")
        return {
            "ok": not problems,
            "problems": problems,
            "model": rates.model,
            "reasoning_effort": effort or "medium",
            "pricing_last_updated": rates.last_updated.isoformat(),
            "long_context_threshold_tokens": rates.long_context_threshold_tokens,
            "long_context_configured": rates.long_context_configured,
            "rates": {
                "input_per_million": rates.input_per_million,
                "cached_input_per_million": rates.cached_input_per_million,
                "cache_write_per_million": rates.cache_write_per_million,
                "output_per_million": rates.output_per_million,
                "web_search_per_call": rates.web_search_per_call,
                "long_context_input_per_million": rates.long_context_input_per_million,
                "long_context_output_per_million": rates.long_context_output_per_million,
            },
        }

    def requires_long_context(self, input_tokens: int, model: str | None = None) -> bool:
        rates = self.pricing_for_model(model)
        thr = rates.long_context_threshold_tokens
        return bool(thr and int(input_tokens or 0) >= int(thr))

    def estimate_next_call_cost_usd(
        self,
        *,
        call_type: str = "outreach_slot",
        model: str | None = None,
    ) -> dict[str, Any]:
        """Conservative local estimate for one additional Responses call."""
        if call_type in {"master_discovery", "replacement_stage_a"}:
            in_tok = int(getattr(self.settings, "discovery_estimate_stage_a_prompt_tokens_fallback", 2500) or 2500)
            out_tok = int(getattr(self.settings, "discovery_estimate_stage_a_output_tokens", 2500) or 2500)
            searches = int(getattr(self.settings, "discovery_estimate_stage_a_searches", 6) or 6)
        else:
            in_tok = int(getattr(self.settings, "discovery_estimate_slot_prompt_tokens_fallback", 2500) or 2500)
            out_tok = int(getattr(self.settings, "discovery_estimate_slot_output_tokens", 3500) or 3500)
            searches = int(getattr(self.settings, "discovery_estimate_slot_searches", 4) or 4)
        return self.compute_costs(
            input_tokens=in_tok,
            output_tokens=out_tok,
            web_search_calls=searches,
            model=model,
        )

    def compute_costs(
        self,
        *,
        input_tokens: int = 0,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        output_tokens: int = 0,
        web_search_calls: int = 0,
        model: str | None = None,
        # reasoning_tokens are informational only — already inside output_tokens.
        reasoning_tokens: int = 0,
    ) -> dict[str, Any]:
        del reasoning_tokens  # never bill separately
        rates = self.pricing_for_model(model)
        cached = max(0, int(cached_tokens or 0))
        cache_write = max(0, int(cache_write_tokens or 0))
        total_in = max(0, int(input_tokens or 0))
        ordinary = max(0, total_in - cached - cache_write)
        out = max(0, int(output_tokens or 0))
        searches = max(0, int(web_search_calls or 0))

        pricing_meta = {
            "model": rates.model,
            "input_per_million": rates.input_per_million,
            "cached_input_per_million": rates.cached_input_per_million,
            "cache_write_per_million": rates.cache_write_per_million,
            "output_per_million": rates.output_per_million,
            "web_search_per_call": rates.web_search_per_call,
            "long_context_threshold_tokens": rates.long_context_threshold_tokens,
            "long_context_configured": rates.long_context_configured,
            "last_updated": rates.last_updated.isoformat(),
        }

        # Long-context tier required but rates missing → NEVER invent $0.
        if (
            rates.long_context_threshold_tokens
            and total_in >= rates.long_context_threshold_tokens
            and not rates.long_context_configured
        ):
            return {
                "pricing_status": PRICING_UNKNOWN_LONG_CONTEXT,
                "ordinary_input_tokens": ordinary,
                "cached_input_tokens": cached,
                "cache_write_tokens": cache_write,
                "input_tokens": total_in,
                "output_tokens": out,
                "web_search_calls": searches,
                "input_cost_usd": None,
                "output_cost_usd": None,
                "web_search_cost_usd": None,
                "total_cost_usd": None,
                "estimated_cost_available": False,
                "pricing": pricing_meta,
                "message": (
                    "Cost estimate unavailable because long-context pricing is not configured "
                    f"(input_tokens={total_in} ≥ threshold={rates.long_context_threshold_tokens})."
                ),
            }

        input_rate = rates.input_per_million
        output_rate = rates.output_per_million
        used_long = False
        if (
            rates.long_context_threshold_tokens
            and total_in >= rates.long_context_threshold_tokens
            and rates.long_context_configured
        ):
            input_rate = float(rates.long_context_input_per_million or input_rate)
            output_rate = float(rates.long_context_output_per_million or output_rate)
            used_long = True

        input_cost = (
            ordinary * input_rate
            + cached * rates.cached_input_per_million
            + cache_write * rates.cache_write_per_million
        ) / 1_000_000.0
        output_cost = (out * output_rate) / 1_000_000.0
        web_cost = searches * rates.web_search_per_call
        total = input_cost + output_cost + web_cost
        return {
            "pricing_status": PRICING_OK,
            "ordinary_input_tokens": ordinary,
            "cached_input_tokens": cached,
            "cache_write_tokens": cache_write,
            "input_tokens": total_in,
            "output_tokens": out,
            "web_search_calls": searches,
            "input_cost_usd": round(input_cost, 6),
            "output_cost_usd": round(output_cost, 6),
            "web_search_cost_usd": round(web_cost, 6),
            "total_cost_usd": round(total, 6),
            "estimated_cost_available": True,
            "used_long_context_rates": used_long,
            "pricing": pricing_meta,
        }
