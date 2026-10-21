"""Zero-API-call Acquisition preflight + tiktoken cost estimation."""

from __future__ import annotations

import logging
from typing import Any

from acquisition.config import Settings, get_settings
from acquisition.services.acquisition_pricing import (
    PRICING_OK,
    PRICING_UNKNOWN_LONG_CONTEXT,
    AcquisitionPricingService,
    PRICING_LAST_UPDATED,
)
from acquisition.services.agentic_discovery_json_schemas import (
    MASTER_DISCOVERY_SCHEMA,
    OUTREACH_SLOT_SCHEMA,
    master_discovery_text_format,
    outreach_slot_text_format,
    validate_schema_shape,
)
from acquisition.services.discovery_prompt_builder import DiscoveryPromptBuilder
from acquisition.services.priority_score import ScoringWeightsError, validate_scoring_weights

logger = logging.getLogger(__name__)


def _tiktoken_count(text: str, model: str) -> tuple[int, str, str]:
    """Return (token_count, encoding_name, confidence). Never calls OpenAI."""
    try:
        import tiktoken
    except ImportError:
        approx = max(1, len(text) // 4)
        return approx, "char_heuristic_4", "low"

    enc_name = "o200k_base"
    confidence = "high"
    try:
        enc = tiktoken.encoding_for_model(model)
        enc_name = getattr(enc, "name", model)
    except Exception:  # noqa: BLE001
        try:
            enc = tiktoken.get_encoding("o200k_base")
            enc_name = "o200k_base"
            confidence = "medium"
        except Exception:  # noqa: BLE001
            approx = max(1, len(text) // 4)
            return approx, "char_heuristic_4", "low"
    try:
        return len(enc.encode(text or "")), enc_name, confidence
    except Exception:  # noqa: BLE001
        approx = max(1, len(text) // 4)
        return approx, enc_name, "low"


class AcquisitionCostEstimator:
    """Preflight validation + local token/cost estimates (no OpenAI HTTP)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.pricing = AcquisitionPricingService(self.settings)

    def planned_api_requests(self, requested_count: int) -> dict[str, int]:
        requested = max(1, int(requested_count))
        base = 1 + requested  # 1 Stage A + N slots
        max_attempts = max(1, int(getattr(self.settings, "openai_discovery_max_attempts", 3) or 3))
        stage_a_extra = max(0, max_attempts - 1)
        slot_retries = max(0, int(getattr(self.settings, "enrichment_max_retries", 1) or 0))
        replacement_rounds = max(0, int(getattr(self.settings, "discovery_replacement_max_rounds", 3) or 0))
        max_slot_calls = requested * (1 + slot_retries)
        max_replacement_calls = replacement_rounds * requested * (1 + slot_retries)
        max_stage_a = 1 + stage_a_extra + replacement_rounds * max_attempts
        configured_max = max_stage_a + max_slot_calls + max_replacement_calls
        return {
            "requested_count": requested,
            "base_calls": base,
            "stage_a_base": 1,
            "slot_base": requested,
            "configured_max_calls": configured_max,
            "openai_discovery_max_attempts": max_attempts,
            "enrichment_max_retries": slot_retries,
            "discovery_replacement_max_rounds": replacement_rounds,
            "note": (
                "Configured maximum is theoretical worst-case. "
                "DISCOVERY_MAX_ACTUAL_RUN_COST_USD may stop additional calls earlier."
            ),
        }

    def build_prompts(
        self,
        admin: dict[str, Any],
        *,
        requested_count: int,
        coverage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        coverage = coverage or {}
        stage_a = DiscoveryPromptBuilder.build_discovery(
            admin,
            coverage,
            remaining_count=requested_count,
            already_found=[],
            attempt=1,
            max_attempts=int(getattr(self.settings, "openai_discovery_max_attempts", 3) or 3),
            reserved_domains=list(coverage.get("do_not_rediscover_domains") or [])[:50],
            scoring_settings=admin.get("score_weights") or {},
        )
        seed = {
            "company_name": "Example Seed Co",
            "website": "https://example-seed.example",
            "normalized_domain": "example-seed.example",
            "location": {"city": "Austin", "state": "TX", "country": "US"},
            "industry": "management consulting",
            "business_type": "management consulting firms",
            "description": "Representative seed for token estimation only.",
            "initial_evidence_urls": ["https://example-seed.example/about"],
        }
        slot = DiscoveryPromptBuilder.build_outreach_slot(
            admin,
            admin.get("score_weights") or {},
            slot_id="slot-estimate-1",
            seed=seed,
            reserved_domains=list(coverage.get("do_not_rediscover_domains") or [])[:50],
            max_candidates=int(getattr(self.settings, "outreach_slot_max_candidates", 3) or 3),
            coverage_context=coverage,
        )
        return {"stage_a": stage_a, "slot": slot}

    def preflight(
        self,
        admin: dict[str, Any],
        *,
        requested_count: int | None = None,
        coverage: dict[str, Any] | None = None,
        api_key_configured: bool = False,
    ) -> dict[str, Any]:
        """Validate configuration + estimate cost. ZERO OpenAI HTTP calls."""
        checks: list[dict[str, Any]] = []
        problems: list[str] = []

        def _check(name: str, ok: bool, detail: str = "", *, fail_detail: str | None = None) -> None:
            if ok:
                checks.append({"name": name, "ok": True, "detail": detail})
            else:
                msg = fail_detail if fail_detail is not None else (detail or "failed")
                checks.append({"name": name, "ok": False, "detail": msg})
                problems.append(f"{name}: {msg}")

        requested = int(requested_count or admin.get("discovery_cycle_limit") or 5)
        requested = max(1, min(requested, int(getattr(self.settings, "discovery_cycle_hard_max", 25) or 25)))

        _check("Admin ICP loaded", bool(admin), "loaded", fail_detail="missing admin settings")
        _check(
            "Exclusions loaded",
            isinstance(admin.get("excluded_categories"), list),
            f"{len(admin.get('excluded_categories') or [])} categories",
            fail_detail="excluded_categories missing",
        )
        try:
            validate_scoring_weights(admin.get("score_weights"))
            _check("Weights = 100%", True, "fit+timing+reachability")
        except ScoringWeightsError as exc:
            _check("Weights = 100%", False, str(exc))

        weights = admin.get("score_weights") or {}
        try:
            prio = float(weights.get("ready_for_outreach_min_priority", 55))
            reach = float(weights.get("ready_for_outreach_min_reachability", 40))
            _check("Ready thresholds", 0 <= prio <= 100 and 0 <= reach <= 100, f"{prio}/{reach}")
        except (TypeError, ValueError):
            _check("Ready thresholds", False, "non-numeric thresholds")

        stage_a_schema_problems = validate_schema_shape(MASTER_DISCOVERY_SCHEMA)
        slot_schema_problems = validate_schema_shape(OUTREACH_SLOT_SCHEMA)
        stage_a_schema_problems = [p for p in stage_a_schema_problems if "anyOf" not in p]
        slot_schema_problems = [
            p for p in slot_schema_problems if "prospect.anyOf" not in p or "additionalProperties" not in p
        ]
        _check("Stage A schema valid", not stage_a_schema_problems, "; ".join(stage_a_schema_problems[:3]))
        _check("Slot schema valid", not slot_schema_problems, "; ".join(slot_schema_problems[:3]))
        _check("Stage A text.format ready", bool(master_discovery_text_format()["format"]["strict"]))
        _check("Slot text.format ready", bool(outreach_slot_text_format()["format"]["strict"]))

        model = (getattr(self.settings, "openai_discovery_model", "") or "").strip()
        effort = (getattr(self.settings, "openai_discovery_reasoning_effort", "") or "medium").strip()
        _check("Model configured", bool(model), model or "", fail_detail="OPENAI_DISCOVERY_MODEL empty")
        pricing_cfg = self.pricing.validate_config(model=model)
        _check(
            "Pricing config valid",
            pricing_cfg["ok"],
            f"updated {pricing_cfg['pricing_last_updated']}",
            fail_detail="; ".join(pricing_cfg["problems"][:4]) or "invalid pricing",
        )
        _check(
            "Reasoning effort supported",
            not any("reasoning effort" in p for p in pricing_cfg["problems"]),
            effort,
            fail_detail="; ".join(p for p in pricing_cfg["problems"] if "reasoning" in p) or effort,
        )
        _check(
            "OpenAI key configured",
            bool(api_key_configured),
            "present",
            fail_detail="missing credentials",
        )
        concurrency = max(1, int(getattr(self.settings, "enrichment_max_concurrency", 5) or 5))
        slot_max = max(1, int(getattr(self.settings, "outreach_slot_max_candidates", 3) or 3))
        _check("Concurrency configured", concurrency >= 1, f"ENRICHMENT_MAX_CONCURRENCY={concurrency}")
        _check("Slot max candidates", slot_max >= 1, f"OUTREACH_SLOT_MAX_CANDIDATES={slot_max}")
        _check("Circuit breaker enabled", True, "majority systemic tech failures halt replacements")

        prompts = self.build_prompts(admin, requested_count=requested, coverage=coverage)
        stage_text = (prompts["stage_a"]["instructions"] or "") + "\n" + (prompts["stage_a"]["prompt"] or "")
        slot_text = (prompts["slot"]["instructions"] or "") + "\n" + (prompts["slot"]["prompt"] or "")
        stage_tokens, stage_enc, stage_conf = _tiktoken_count(stage_text, model or "gpt-4o")
        slot_tokens, slot_enc, slot_conf = _tiktoken_count(slot_text, model or "gpt-4o")

        # Quality-prompt smoke checks (local strings only).
        stage_prompt_l = (prompts["stage_a"]["prompt"] or "").lower()
        stage_quality_ok = (
            "do not return the first" in stage_prompt_l
            and "search broadly" in stage_prompt_l
            and "compare" in stage_prompt_l
        )
        _check(
            "Stage A quality-selection language",
            stage_quality_ok,
            "broad search + compare + reject-first-N",
            fail_detail="missing broad-search/compare language",
        )
        slot_prompt_l = (prompts["slot"]["prompt"] or "").lower()
        slot_obj_ok = (
            "exactly one final prospect" in slot_prompt_l or "one final prospect" in slot_prompt_l
        )
        _check(
            "Slot outreach-ready objective",
            slot_obj_ok,
            "one final outreach-ready prospect",
            fail_detail="missing one-final-prospect objective",
        )

        plan = self.planned_api_requests(requested)
        expected_stage_out = int(getattr(self.settings, "discovery_estimate_stage_a_output_tokens", 2500) or 2500)
        expected_slot_out = int(getattr(self.settings, "discovery_estimate_slot_output_tokens", 3500) or 3500)
        expected_stage_search = int(getattr(self.settings, "discovery_estimate_stage_a_searches", 6) or 6)
        expected_slot_search = int(getattr(self.settings, "discovery_estimate_slot_searches", 4) or 4)
        max_in_assum = int(
            getattr(self.settings, "discovery_estimate_max_input_tokens_per_call", 50_000) or 50_000
        )
        rates = self.pricing.pricing_for_model(model)
        thr = rates.long_context_threshold_tokens or 0
        # Conservative: assume tool/content tokens may inflate input up to configured ceiling.
        worst_input = max(stage_tokens, slot_tokens, max_in_assum)
        long_ctx_risk = bool(thr and worst_input >= thr)
        if long_ctx_risk and not rates.long_context_configured:
            _check(
                "Long-context pricing",
                False,
                (
                    "Cost estimate unavailable because long-context pricing is not configured "
                    f"(assumed max input {worst_input} ≥ threshold {thr})."
                ),
            )
            cost_available = False
            pricing_status = PRICING_UNKNOWN_LONG_CONTEXT
        elif thr and max(stage_tokens, slot_tokens) >= thr and not rates.long_context_configured:
            _check(
                "Long-context pricing",
                False,
                "prompt tokens already at/above long-context threshold without rates",
            )
            cost_available = False
            pricing_status = PRICING_UNKNOWN_LONG_CONTEXT
        else:
            # Known below threshold for prompt size; ceiling may still warn but allow if ceiling < thr
            # or long-context configured. If ceiling >= thr without rates, block (conservative).
            if long_ctx_risk and not rates.long_context_configured:
                cost_available = False
                pricing_status = PRICING_UNKNOWN_LONG_CONTEXT
            else:
                _check(
                    "Long-context pricing",
                    True,
                    (
                        f"configured"
                        if rates.long_context_configured
                        else f"run assumed below threshold ({thr}); prompt max={max(stage_tokens, slot_tokens)}"
                    ),
                )
                cost_available = True
                pricing_status = PRICING_OK

        stage_cost = self.pricing.compute_costs(
            input_tokens=stage_tokens,
            output_tokens=expected_stage_out,
            web_search_calls=expected_stage_search,
            model=model,
        )
        one_slot_cost = self.pricing.compute_costs(
            input_tokens=slot_tokens,
            output_tokens=expected_slot_out,
            web_search_calls=expected_slot_search,
            model=model,
        )
        if (
            stage_cost.get("pricing_status") != PRICING_OK
            or one_slot_cost.get("pricing_status") != PRICING_OK
            or not cost_available
        ):
            cost_available = False
            pricing_status = PRICING_UNKNOWN_LONG_CONTEXT
            base_total = None
            high_total = None
            _check(
                "Cost estimate available",
                False,
                "Cost estimate unavailable because long-context pricing is not configured.",
            )
        else:
            base_total = round(
                float(stage_cost["total_cost_usd"]) + requested * float(one_slot_cost["total_cost_usd"]),
                6,
            )
            avg_call = (float(stage_cost["total_cost_usd"]) + float(one_slot_cost["total_cost_usd"])) / 2.0
            extra = max(0, plan["configured_max_calls"] - plan["base_calls"])
            high_total = round(base_total + 0.5 * extra * avg_call, 6)
            _check("Cost estimate available", True, f"base=${base_total:.4f}")

        max_est = getattr(self.settings, "discovery_max_estimated_run_cost_usd", None)
        max_act = getattr(self.settings, "discovery_max_actual_run_cost_usd", None)
        budget_block = False
        if not cost_available:
            budget_block = True
            problems.append("Cost estimate unavailable — cannot authorize a paid run")
        elif max_est is not None and float(max_est) > 0 and base_total is not None and base_total > float(max_est):
            budget_block = True
            problems.append(
                f"Estimated run cost: ${base_total:.4f}. Configured pre-run limit: ${float(max_est):.2f}. "
                "RUN BLOCKED — estimated cost exceeds configured budget."
            )
            _check(
                "Estimated cost within budget",
                False,
                f"${base_total:.4f} > ${float(max_est):.2f}",
            )
        else:
            _check(
                "Estimated cost within budget",
                True,
                "no limit" if not max_est else f"${base_total:.4f} ≤ ${float(max_est):.2f}",
            )

        _check(
            "Actual cost budget configured",
            True,
            "no limit" if not max_act else f"DISCOVERY_MAX_ACTUAL_RUN_COST_USD={float(max_act):.2f}",
        )
        _check("Cost estimator ready", True, f"tiktoken={stage_enc}/{slot_enc} conf={stage_conf}/{slot_conf}")

        # Critical unknown checks must not say Safe YES.
        critical_unknown = any(
            (not c["ok"])
            and c["name"]
            in {
                "Cost estimate available",
                "Long-context pricing",
                "Pricing config valid",
                "Stage A schema valid",
                "Slot schema valid",
                "Model configured",
                "OpenAI key configured",
                "Weights = 100%",
                "Estimated cost within budget",
            }
            for c in checks
        )
        ok = not problems and not critical_unknown
        safe = ok and not budget_block and cost_available

        token_cost_base = None
        web_cost_base = None
        if cost_available and stage_cost.get("total_cost_usd") is not None:
            token_cost_base = round(
                float(stage_cost["input_cost_usd"] or 0)
                + float(stage_cost["output_cost_usd"] or 0)
                + requested
                * (
                    float(one_slot_cost["input_cost_usd"] or 0)
                    + float(one_slot_cost["output_cost_usd"] or 0)
                ),
                6,
            )
            web_cost_base = round(
                float(stage_cost["web_search_cost_usd"] or 0)
                + requested * float(one_slot_cost["web_search_cost_usd"] or 0),
                6,
            )

        return {
            "ok": ok,
            "safe_to_run": safe,
            "problems": problems,
            "checks": checks,
            "requested_count": requested,
            "model": model,
            "reasoning_effort": effort,
            "concurrency": concurrency,
            "outreach_slot_max_candidates": slot_max,
            "plan": plan,
            "tokens": {
                "stage_a_prompt_tokens": stage_tokens,
                "slot_prompt_tokens": slot_tokens,
                "slots_prompt_tokens_total": slot_tokens * requested,
                "base_prompt_tokens_total": stage_tokens + slot_tokens * requested,
                "stage_a_encoding": stage_enc,
                "slot_encoding": slot_enc,
                "stage_a_confidence": stage_conf,
                "slot_confidence": slot_conf,
                "tiktoken_note": (
                    "gpt-6-luna has no native tiktoken mapping in this install; "
                    "using o200k_base fallback with medium confidence"
                    if stage_conf == "medium"
                    else None
                ),
            },
            "assumptions": {
                "stage_a_expected_output_tokens": expected_stage_out,
                "slot_expected_output_tokens": expected_slot_out,
                "stage_a_expected_searches": expected_stage_search,
                "slot_expected_searches": expected_slot_search,
                "max_input_tokens_per_call_assumption": max_in_assum,
                "long_context_threshold_tokens": thr,
                "note": (
                    "tiktoken estimates prompt text only. Reasoning tokens, output length, "
                    "and web-search content are assumed from defaults — not exact. "
                    "Budget may prevent reaching the theoretical maximum API call count."
                ),
            },
            "budgets": {
                "max_estimated_run_cost_usd": float(max_est) if max_est else None,
                "max_actual_run_cost_usd": float(max_act) if max_act else None,
            },
            "estimate": {
                "pricing_status": pricing_status,
                "cost_available": cost_available,
                "stage_a_usd": stage_cost.get("total_cost_usd"),
                "one_slot_usd": one_slot_cost.get("total_cost_usd"),
                "base_run_usd": base_total,
                "possible_high_usd": high_total,
                "token_cost_usd": token_cost_base,
                "web_search_cost_usd": web_cost_base,
                "pricing_last_updated": PRICING_LAST_UPDATED.isoformat(),
                "stage_a_breakdown": stage_cost,
                "one_slot_breakdown": one_slot_cost,
                "message": None
                if cost_available
                else "Cost estimate unavailable because long-context pricing is not configured.",
            },
            "structured_outputs": {
                "stage_a": master_discovery_text_format()["format"],
                "slot": outreach_slot_text_format()["format"],
            },
        }
