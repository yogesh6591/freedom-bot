"""Parallel outreach-ready ProspectSlot orchestration for agentic acquisition.

Stage A seeds → N slots (requested_count) → one agentic OpenAI call per slot
(research seed / reject / find alternatives) → backend-authoritative ready gate.
"""

from __future__ import annotations

import contextvars
import logging
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import Settings
from acquisition.integrations.discovery.openai_discovery_client import OpenAIDiscoveryClient
from acquisition.models.prospect import AcquisitionStatus, Prospect
from acquisition.services import live_trace
from acquisition.services.agentic_discovery_schema import (
    StructuredOutputValidationError,
    evaluate_outreach_ready,
    has_decision_maker,
    has_legitimate_contact_path,
    is_transient_enrichment_error,
    is_usable_prospect,
    matches_exclusion,
    parse_slot_result,
)
from acquisition.services.acquisition_pricing import AcquisitionPricingService
from acquisition.services.discovery_cost_budget import DiscoveryCostBudget
from acquisition.services.discovery_prompt_builder import DiscoveryPromptBuilder
from acquisition.services.outreach_slot import (
    SLOT_COLLISION_RETRY,
    SLOT_EXHAUSTED,
    SLOT_PENDING,
    SLOT_READY,
    SLOT_RESEARCHING,
    SLOT_TECHNICAL_FAILURE,
    ProspectSlot,
)
from acquisition.services.priority_score import calculate_priority_score
from acquisition.services.prospect_identity import normalize_domain

logger = logging.getLogger(__name__)


def _technical_fingerprint(reason: str | None) -> str:
    text = (reason or "").strip().lower()
    if not text:
        return "unknown_technical"
    if "fit_score" in text or "timing_score" in text or "reachability_score" in text:
        return "schema_score_shape"
    if "enrichment scoring" in text or "expected integer" in text or "schema" in text:
        return "schema_validation"
    if text.startswith("http_4"):
        return text.split(":", 1)[0][:24]
    if text.startswith("http_520") or text.startswith("http_5"):
        return text.split(":", 1)[0][:24]
    if "malformed" in text or "empty_result" in text:
        return "malformed_or_empty"
    # Collapse noisy dict dumps to a stable key.
    compact = re.sub(r"\s+", " ", text)[:80]
    return compact


def _is_systemic_fingerprint(fp: str) -> bool:
    return fp.startswith("schema") or fp.startswith("http_4") or fp in {
        "malformed_or_empty",
        "openai_discovery_not_configured",
    }


class OutreachSlotOrchestrator:
    def __init__(
        self,
        *,
        db: Session,
        settings: Settings,
        client: OpenAIDiscoveryClient,
        persist_one,
        discover_with_topup,
    ) -> None:
        self.db = db
        self.settings = settings
        self.client = client
        self._persist_one = persist_one
        self._discover_with_topup = discover_with_topup

    def run(
        self,
        *,
        admin: dict[str, Any],
        context: dict[str, Any],
        requested: int,
        max_attempts: int,
        concurrency: int,
        max_retries: int,
        fit_w: float,
        timing_w: float,
        reach_w: float,
    ) -> dict[str, Any]:
        max_candidates = max(1, int(getattr(self.settings, "outreach_slot_max_candidates", 3) or 3))
        replacement_max = max(0, int(getattr(self.settings, "discovery_replacement_max_rounds", 3) or 0))
        weights = admin.get("score_weights") or {}
        ready_min_priority = float(weights.get("ready_for_outreach_min_priority", 55))
        ready_min_reach = float(weights.get("ready_for_outreach_min_reachability", 40))
        excluded = [str(x) for x in (admin.get("excluded_categories") or [])]
        pricing = AcquisitionPricingService(self.settings)
        budget = DiscoveryCostBudget(
            getattr(self.settings, "discovery_max_actual_run_cost_usd", None)
        )

        live_trace.event(
            "run",
            f"Requested FINAL outreach-ready: {requested}",
            level="info",
            requested_final_outreach_ready=requested,
            max_candidates_per_slot=max_candidates,
            budget_limit=budget.max_actual_usd,
        )

        seeds, attempt_log, cost_usd, errors = self._discover_with_topup(
            admin=admin,
            context=context,
            requested=requested,
            max_attempts=max_attempts,
        )
        # Stage A already completed — apply only known costs; never treat unknown as $0.
        stage_incomplete = any(
            (isinstance(a, dict) and (a.get("cost_complete") is False or a.get("pricing_status") == "unknown_long_context_rate"))
            for a in (attempt_log or [])
        )
        if stage_incomplete:
            budget.mark_pricing_unknown()
            live_trace.event(
                "error",
                "Actual cost unavailable — long-context pricing is not configured.",
                level="error",
                stop_reason="pricing_unknown",
                pricing_status="unknown_long_context_rate",
                **budget.snapshot(),
            )
            enrich_stats_boot = {
                "system_error": "pricing_unknown; long-context pricing is not configured",
                "system_error_fingerprint": "pricing_unknown",
            }
        else:
            budget.add_actual(float(cost_usd or 0.0))
            enrich_stats_boot = {}
        if not seeds:
            live_trace.event("error", "No seed companies discovered", level="error")
            return {
                "ok": False,
                "stop_reason": "no_prospects_returned",
                "errors": errors or ["no_seed_companies"],
                "requested_count": requested,
                "found_count": 0,
                "usable_count": 0,
                "ready_count": 0,
                "shortfall_count": requested,
                "shortfall_reason": "openai_returned_no_companies",
                "created": 0,
                "updated": 0,
                "duplicates": 0,
                "excluded": 0,
                "candidates": 0,
                "results": [],
                "usable_results": [],
                "cost_usd": cost_usd,
                "discovery_attempts": attempt_log,
                "search_attempts": len(attempt_log),
                "slots": [],
                "enrichment": {},
            }

        # Pad slots to requested_count even if seed shortfall — empty seed slots search freely.
        while len(seeds) < requested:
            seeds.append(None)  # type: ignore[arg-type]

        slots = [ProspectSlot.from_seed(i, seeds[i] if i < len(seeds) else None) for i in range(requested)]
        live_trace.event(
            "plan",
            f"MASTER DISCOVERY: strong seeds found {sum(1 for s in seeds if s)}/{requested}; "
            f"created {len(slots)} outreach verification slots",
            level="success",
            seeds=sum(1 for s in seeds if s),
            slots=len(slots),
            seed_companies=[
                {"company": (s or {}).get("company_name"), "domain": (s or {}).get("normalized_domain")}
                for s in seeds
                if s
            ][:40],
        )

        reserved = self._build_reserved_domains(seeds)
        accepted_ready: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        usable_results: list[dict[str, Any]] = []
        terminal: list[dict[str, Any]] = []
        created = updated = duplicates = excluded_n = 0
        score_rows: list[dict[str, Any]] = []
        completeness = {
            "with_email": 0,
            "with_phone": 0,
            "with_dm": 0,
            "with_revenue": 0,
            "with_headcount": 0,
        }
        enrich_stats = {
            "started": 0,
            "completed": 0,
            "failed": 0,
            "schema_validation_failures": 0,
            "transient_failures": 0,
            "retries_attempted": 0,
            "recovered_after_retry": 0,
            "ready": 0,
            "needs_research": 0,
            "exhausted": 0,
            "technical_failure": 0,
            "dm_found": 0,
            "dm_unknown": 0,
            "duplicate_collisions": 0,
            "replacement_slots": 0,
            "companies_researched": 0,
            "rejected_during_slots": 0,
            "max_concurrency": concurrency,
            "max_retries": max_retries,
            "max_candidates_per_slot": max_candidates,
            "observed_peak_concurrency": 0,
            "system_error": None,
            "system_error_fingerprint": None,
        }
        enrich_stats.update(enrich_stats_boot)

        replacement_round = 0
        pending = [s for s in slots if s.status in {SLOT_PENDING, SLOT_COLLISION_RETRY}]
        max_loop_iters = max(requested * 4, replacement_max * 2 + 2)
        loop_iters = 0
        collision_budget: dict[str, int] = {}

        while pending and len(accepted_ready) < requested and loop_iters < max_loop_iters:
            loop_iters += 1
            live_trace.event(
                "research",
                f"Parallel Outreach Verification Slots: launching {len(pending)} slot job(s)",
                level="info",
                slots=[s.slot_id for s in pending],
                reserved_domains=len(reserved),
                loop_iters=loop_iters,
            )
            # Reserve estimated cost before launching parallel workers.
            # Only launch as many calls as fit remaining budget (and concurrency).
            slot_est = pricing.estimate_next_call_cost_usd(call_type="outreach_slot")
            est_one = (
                float(slot_est.get("total_cost_usd") or 0.0)
                if slot_est.get("estimated_cost_available")
                else 0.05
            )
            if budget.pricing_unknown:
                for slot in pending:
                    slot.status = SLOT_TECHNICAL_FAILURE
                    slot.failure_reason = "pricing_unknown"
                enrich_stats["system_error"] = (
                    "pricing_unknown; Actual cost unavailable — long-context pricing is not configured."
                )
                enrich_stats["system_error_fingerprint"] = "pricing_unknown"
                live_trace.event(
                    "error",
                    "Actual cost unavailable — long-context pricing is not configured.",
                    level="error",
                    stop_reason="pricing_unknown",
                    **budget.snapshot(),
                )
                pending = []
                break

            max_affordable = budget.max_new_calls(est_one)
            # Cap wave by concurrency AND remaining reserved budget; keep leftover slots pending.
            max_this_wave = min(len(pending), max(0, concurrency), max_affordable)
            launchable: list[ProspectSlot] = []
            reservations: dict[str, str] = {}  # slot_id -> reservation_id
            for slot in pending:
                if len(launchable) >= max_this_wave:
                    break
                rid = budget.try_reserve(est_one, reservation_id=f"{slot.slot_id}-w{loop_iters}")
                if rid is None:
                    break
                launchable.append(slot)
                reservations[slot.slot_id] = rid
            deferred = [s for s in pending if s not in launchable]

            if not launchable:
                # No additional paid call safely fits (or pricing unknown). Do not cancel
                # in-flight work (there is none here); fail remaining pending seats.
                reason = budget.stop_reason or (
                    "pricing_unknown" if budget.pricing_unknown else "cost_budget_reached"
                )
                for slot in deferred:
                    if slot.status in {SLOT_PENDING, SLOT_COLLISION_RETRY}:
                        slot.status = SLOT_TECHNICAL_FAILURE
                        slot.failure_reason = reason
                enrich_stats["system_error"] = (
                    f"{reason}; actual=${budget.actual_cost_usd:.4f} "
                    f"reserved=${budget.reserved_usd:.4f} "
                    f"limit=${float(budget.max_actual_usd or 0):.4f}; "
                    f"skipped_launches={len(deferred)}; est_per_call=${est_one:.4f}"
                )
                enrich_stats["system_error_fingerprint"] = reason
                enrich_stats["budget"] = budget.snapshot()
                msg = (
                    "Actual cost unavailable — long-context pricing is not configured."
                    if reason == "pricing_unknown"
                    else "Cost budget reached — not launching additional slot API calls"
                )
                live_trace.event(
                    "error",
                    msg,
                    level="error",
                    stop_reason=reason,
                    skipped_launches=len(deferred),
                    estimated_per_call_usd=est_one,
                    max_affordable=max_affordable,
                    **budget.snapshot(),
                )
                pending = []
                break

            if deferred:
                live_trace.event(
                    "research",
                    f"Wave capped at {len(launchable)} of {len(pending)} "
                    f"(concurrency={concurrency}, budget_affordable={max_affordable}); "
                    f"{len(deferred)} slot(s) remain queued",
                    level="info",
                    launched=len(launchable),
                    deferred=len(deferred),
                    concurrency=concurrency,
                    max_affordable=max_affordable,
                    estimated_per_call_usd=est_one,
                    **budget.snapshot(),
                )

            pre_wave_cost = {id(s): float(s.cost_usd or 0.0) for s in launchable}
            try:
                self._run_slots_parallel(
                    launchable,
                    admin=admin,
                    context=context,
                    reserved=reserved,
                    max_candidates=max_candidates,
                    concurrency=concurrency,
                    max_retries=max_retries,
                    enrich_stats=enrich_stats,
                    budget=budget,
                    estimated_retry_usd=est_one,
                    reservations=reservations,
                )
            finally:
                # Any reservation not settled by a worker must be released.
                for sid, rid in list(reservations.items()):
                    budget.release_if_open(rid)
            # Only count cost accrued in THIS wave (collision-retry slots keep cumulative slot.cost_usd).
            wave_cost = sum(
                max(0.0, float(s.cost_usd or 0.0) - float(pre_wave_cost.get(id(s), 0.0)))
                for s in launchable
            )
            cost_usd += wave_cost
            if budget.pricing_unknown:
                enrich_stats["system_error"] = (
                    "pricing_unknown; Actual cost unavailable — long-context pricing is not configured."
                )
                enrich_stats["system_error_fingerprint"] = "pricing_unknown"
                live_trace.event(
                    "error",
                    "Actual cost unavailable — long-context pricing is not configured.",
                    level="error",
                    stop_reason="pricing_unknown",
                    **budget.snapshot(),
                )
                # Preserve completed results; block only NEW paid calls (fail remaining queued).
                for slot in deferred:
                    if slot.status in {SLOT_PENDING, SLOT_COLLISION_RETRY}:
                        slot.status = SLOT_TECHNICAL_FAILURE
                        slot.failure_reason = "pricing_unknown"
                pending = []
                break

            # Authoritative accept / collision handling
            collision_retries: list[ProspectSlot] = []
            still_queued: list[ProspectSlot] = list(deferred)
            for slot in launchable:
                if slot.status != SLOT_READY or not slot.final_prospect:
                    if slot.status == SLOT_EXHAUSTED:
                        enrich_stats["exhausted"] += 1
                        terminal.append(
                            {
                                "slot_id": slot.slot_id,
                                "terminal": "exhausted",
                                "reason": slot.failure_reason,
                                "rejected": slot.rejected_candidates,
                            }
                        )
                    elif slot.status == SLOT_TECHNICAL_FAILURE:
                        enrich_stats["technical_failure"] += 1
                        terminal.append(
                            {
                                "slot_id": slot.slot_id,
                                "terminal": "technical_failure",
                                "reason": slot.failure_reason,
                            }
                        )
                    enrich_stats["rejected_during_slots"] += len(slot.rejected_candidates or [])
                    enrich_stats["companies_researched"] += int(slot.candidate_attempts or 0)
                    live_trace.event(
                        "research",
                        f"Slot {slot.slot_id}: {slot.status}",
                        level="warning" if slot.status != SLOT_READY else "success",
                        **slot.as_trace(),
                    )
                    continue

                enrich_stats["companies_researched"] += int(slot.candidate_attempts or 0)
                enrich_stats["rejected_during_slots"] += len(slot.rejected_candidates or [])
                prospect = slot.final_prospect
                domain_key = (prospect.get("normalized_domain") or "").strip().lower()

                # Cross-slot / reserved collision
                if domain_key and domain_key in reserved and domain_key not in {
                    ((slot.seed_company or {}).get("normalized_domain") or "").strip().lower()
                }:
                    # If already accepted this domain from another slot, collision.
                    if any(
                        (r.get("domain") or "").strip().lower() == domain_key for r in accepted_ready
                    ):
                        enrich_stats["duplicate_collisions"] += 1
                        collision_budget[slot.slot_id] = int(collision_budget.get(slot.slot_id) or 0) + 1
                        if collision_budget[slot.slot_id] > 2:
                            slot.status = SLOT_EXHAUSTED
                            slot.final_prospect = None
                            slot.failure_reason = f"duplicate_collision_exhausted:{domain_key}"
                        else:
                            slot.status = SLOT_COLLISION_RETRY
                            slot.final_prospect = None
                            slot.ai_recommended_ready = False
                            slot.failure_reason = f"duplicate_collision:{domain_key}"
                            collision_retries.append(slot)
                        live_trace.event(
                            "dedupe",
                            f"Cross-slot duplicate collision on {domain_key}; "
                            f"{'exhausted' if slot.status == SLOT_EXHAUSTED else 'rerunning'} {slot.slot_id}",
                            level="warning",
                            slot_id=slot.slot_id,
                            domain=domain_key,
                        )
                        continue

                hit = matches_exclusion(prospect, excluded)
                if hit:
                    excluded_n += 1
                    slot.status = SLOT_EXHAUSTED
                    slot.failure_reason = f"excluded:{hit}"
                    terminal.append(
                        {"slot_id": slot.slot_id, "terminal": "excluded", "reason": hit}
                    )
                    continue

                gate = evaluate_outreach_ready(
                    prospect,
                    admin,
                    fit_w=fit_w,
                    timing_w=timing_w,
                    reach_w=reach_w,
                )
                if not gate.get("ready"):
                    # Persist researched-but-not-ready for DB visibility; do not count as ready.
                    try:
                        outcome = self._persist_one(
                            prospect,
                            fit_w=fit_w,
                            timing_w=timing_w,
                            reach_w=reach_w,
                            ready_min_priority=ready_min_priority,
                            ready_min_reach=ready_min_reach,
                            force_ready=False,
                            admin=admin,
                        )
                        if outcome.get("created"):
                            created += 1
                        elif not outcome.get("duplicate"):
                            updated += 1
                        results.append(outcome)
                        if has_decision_maker(prospect):
                            enrich_stats["dm_found"] = int(enrich_stats.get("dm_found") or 0) + 1
                        else:
                            enrich_stats["dm_unknown"] = int(enrich_stats.get("dm_unknown") or 0) + 1
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("persist needs_research failed for slot %s", slot.slot_id)
                        errors.append(f"{slot.slot_id}: {exc}")
                        self.db.rollback()
                    slot.status = SLOT_EXHAUSTED
                    slot.failure_reason = gate.get("reason_text") or gate.get("reason_code")
                    enrich_stats["needs_research"] += 1
                    terminal.append(
                        {
                            "slot_id": slot.slot_id,
                            "terminal": "needs_research",
                            "reason": slot.failure_reason,
                            "ai_recommended_ready": slot.ai_recommended_ready,
                        }
                    )
                    live_trace.event(
                        "decision",
                        f"Backend rejected AI-ready for {prospect.get('company_name')}: "
                        f"{slot.failure_reason}",
                        level="warning",
                        slot_id=slot.slot_id,
                        gate=gate,
                    )
                    # Allow global replacement for this missing ready seat.
                    continue

                try:
                    outcome = self._persist_one(
                        prospect,
                        fit_w=fit_w,
                        timing_w=timing_w,
                        reach_w=reach_w,
                        ready_min_priority=ready_min_priority,
                        ready_min_reach=ready_min_reach,
                        force_ready=True,
                        ready_reason=gate.get("reason_text"),
                        admin=admin,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("persist failed for slot %s", slot.slot_id)
                    errors.append(f"{slot.slot_id}: {exc}")
                    self.db.rollback()
                    slot.status = SLOT_TECHNICAL_FAILURE
                    slot.failure_reason = f"persist:{exc}"
                    enrich_stats["technical_failure"] += 1
                    continue

                if outcome.get("duplicate"):
                    duplicates += 1
                    enrich_stats["duplicate_collisions"] += 1
                    collision_budget[slot.slot_id] = int(collision_budget.get(slot.slot_id) or 0) + 1
                    if collision_budget[slot.slot_id] > 2:
                        slot.status = SLOT_EXHAUSTED
                        slot.failure_reason = "duplicate_collision_exhausted"
                        terminal.append(
                            {
                                "slot_id": slot.slot_id,
                                "terminal": "exhausted",
                                "reason": slot.failure_reason,
                            }
                        )
                    else:
                        slot.status = SLOT_COLLISION_RETRY
                        slot.final_prospect = None
                        slot.failure_reason = "database_duplicate"
                        collision_retries.append(slot)
                    if domain_key:
                        reserved.add(domain_key)
                    continue

                if outcome.get("created"):
                    created += 1
                else:
                    updated += 1
                results.append(outcome)
                accepted_ready.append(outcome)
                if domain_key:
                    reserved.add(domain_key)
                enrich_stats["ready"] += 1
                enrich_stats["completed"] += 1

                usable_gate = is_usable_prospect(prospect, admin)
                if usable_gate.get("usable"):
                    usable_results.append(outcome)

                dm = prospect.get("decision_maker") or {}
                contact = prospect.get("contact") or {}
                if has_decision_maker(prospect):
                    completeness["with_dm"] += 1
                    enrich_stats["dm_found"] = int(enrich_stats.get("dm_found") or 0) + 1
                else:
                    enrich_stats["dm_unknown"] = int(enrich_stats.get("dm_unknown") or 0) + 1
                if contact.get("business_email"):
                    completeness["with_email"] += 1
                if contact.get("phone"):
                    completeness["with_phone"] += 1
                if (prospect.get("revenue") or {}).get("value_or_band") or (
                    prospect.get("revenue") or {}
                ).get("exact_amount") is not None:
                    completeness["with_revenue"] += 1
                if (prospect.get("headcount") or {}).get("value_or_band") or (
                    prospect.get("headcount") or {}
                ).get("exact_value") is not None:
                    completeness["with_headcount"] += 1

                score_rows.append(
                    {
                        "company": prospect.get("company_name"),
                        "fit": prospect.get("fit_score"),
                        "timing": prospect.get("timing_score"),
                        "reachability": prospect.get("reachability_score"),
                        "priority": outcome.get("priority_score"),
                        "status": outcome.get("acquisition_status"),
                        "readiness_reason": outcome.get("readiness_reason"),
                        "decision_maker": (dm.get("name") or None),
                        "ready": True,
                        "usable": bool(usable_gate.get("usable")),
                    }
                )
                terminal.append(
                    {
                        "slot_id": slot.slot_id,
                        "terminal": "ready",
                        "company": prospect.get("company_name"),
                        "domain": domain_key,
                    }
                )
                live_trace.event(
                    "research",
                    f"Slot {slot.slot_id} READY: {prospect.get('company_name')}",
                    level="success",
                    **slot.as_trace(),
                    acquisition_status=outcome.get("acquisition_status"),
                )

            # Circuit-breaker: majority same systemic technical error → stop replacements.
            tech_slots = [s for s in launchable if s.status == SLOT_TECHNICAL_FAILURE]
            if len(launchable) >= 2 and len(tech_slots) >= max(2, (len(launchable) + 1) // 2):
                fps = Counter(_technical_fingerprint(s.failure_reason) for s in tech_slots)
                top_fp, top_n = fps.most_common(1)[0]
                if top_n >= max(2, (len(launchable) + 1) // 2) and _is_systemic_fingerprint(top_fp):
                    sample = next(
                        (s.failure_reason for s in tech_slots if _technical_fingerprint(s.failure_reason) == top_fp),
                        top_fp,
                    )
                    enrich_stats["system_error"] = sample
                    enrich_stats["system_error_fingerprint"] = top_fp
                    live_trace.event(
                        "error",
                        f"Slot system error: {top_n}/{len(launchable)} slots failed with "
                        f"{top_fp}: {str(sample)[:240]}",
                        level="error",
                        fingerprint=top_fp,
                        failed_slots=top_n,
                        wave_slots=len(launchable),
                        sample_error=str(sample)[:500],
                        stop_reason="slot_system_error",
                    )
                    # Do not launch replacement rounds for a systemic code/schema failure.
                    pending = []
                    break

            # Global recovery: replace exhausted/failed slots with new seed slots.
            # Keep deferred (budget/concurrency-queued) seats for the next wave after settle.
            pending = list(collision_retries) + list(still_queued)
            ready_n = len(accepted_ready)
            short = requested - ready_n
            if short <= 0:
                break
            if pending:
                # Queued / collision-retry seats remain — next loop recalculates remaining budget.
                continue
            if replacement_round >= replacement_max:
                break

            stage_est = pricing.estimate_next_call_cost_usd(call_type="master_discovery")
            est_stage = (
                float(stage_est.get("total_cost_usd") or 0.0)
                if stage_est.get("estimated_cost_available")
                else 0.08
            )
            if budget.pricing_unknown:
                enrich_stats["system_error"] = (
                    "pricing_unknown; Actual cost unavailable — long-context pricing is not configured."
                )
                enrich_stats["system_error_fingerprint"] = "pricing_unknown"
                live_trace.event(
                    "error",
                    "Actual cost unavailable — long-context pricing is not configured.",
                    level="error",
                    stop_reason="pricing_unknown",
                    **budget.snapshot(),
                )
                pending = []
                break
            stage_rid = budget.try_reserve(est_stage, reservation_id=f"replacement-stage-a-{replacement_round + 1}")
            if stage_rid is None:
                enrich_stats["system_error"] = (
                    f"cost_budget_reached; spent=${budget.actual_cost_usd:.4f} "
                    f"limit=${float(budget.max_actual_usd or 0):.4f}"
                )
                enrich_stats["system_error_fingerprint"] = "cost_budget_reached"
                live_trace.event(
                    "error",
                    "Cost budget reached — skipping additional replacement API calls",
                    level="error",
                    stop_reason="cost_budget_reached",
                    **budget.snapshot(),
                )
                pending = []
                break

            need_new = short
            if need_new > 0:
                replacement_round += 1
                enrich_stats["replacement_slots"] += need_new
                live_trace.event(
                    "harvest",
                    f"Replacement seeds for {need_new} exhausted/failed slot(s) "
                    f"(round {replacement_round}/{replacement_max})",
                    level="info",
                    remaining_ready=short,
                )
                new_seeds, more_attempts, more_cost, more_errors = self._discover_with_topup(
                    admin=admin,
                    context=context,
                    requested=need_new,
                    max_attempts=max_attempts,
                )
                stage_incomplete = any(
                    isinstance(a, dict)
                    and (
                        a.get("cost_complete") is False
                        or a.get("pricing_status") == "unknown_long_context_rate"
                    )
                    for a in (more_attempts or [])
                )
                if stage_incomplete:
                    budget.settle(
                        stage_rid,
                        None,
                        cost_complete=False,
                        pricing_status="unknown_long_context_rate",
                    )
                    enrich_stats["system_error"] = (
                        "pricing_unknown; Actual cost unavailable — long-context pricing is not configured."
                    )
                    enrich_stats["system_error_fingerprint"] = "pricing_unknown"
                    live_trace.event(
                        "error",
                        "Actual cost unavailable — long-context pricing is not configured.",
                        level="error",
                        stop_reason="pricing_unknown",
                        **budget.snapshot(),
                    )
                    pending = []
                    break
                budget.settle(stage_rid, float(more_cost or 0.0), cost_complete=True)
                stage_rid = None
                # Exclude reserved from seed acceptance (discover_with_topup uses its own seen set
                # but we also filter here).
                filtered = []
                for s in new_seeds:
                    d = (s.get("normalized_domain") or "").strip().lower()
                    if d and d in reserved:
                        continue
                    filtered.append(s)
                    if d:
                        reserved.add(d)
                attempt_log.extend(more_attempts)
                cost_usd += more_cost
                errors.extend(more_errors)
                for i, seed in enumerate(filtered[:need_new]):
                    slot = ProspectSlot.from_seed(1000 + replacement_round * 10 + i, seed)
                    slots.append(slot)
                    pending.append(slot)
                # If discovery returned fewer seeds, still create empty-seed slots to search.
                while len(pending) < short:
                    slot = ProspectSlot.from_seed(2000 + len(slots), None)
                    slots.append(slot)
                    pending.append(slot)
                    enrich_stats["replacement_slots"] += 1
            else:
                budget.release_if_open(stage_rid)

            if not pending:
                break

        ready_count = len(accepted_ready)
        shortfall = max(0, requested - ready_count)
        shortfall_reason = None
        stop_reason = "completed"
        if enrich_stats.get("system_error"):
            fp = enrich_stats.get("system_error_fingerprint") or ""
            if fp == "pricing_unknown" or budget.pricing_unknown:
                stop_reason = "pricing_unknown"
            elif fp == "cost_budget_reached" or str(enrich_stats.get("system_error") or "").startswith(
                "cost_budget_reached"
            ):
                stop_reason = "cost_budget_reached"
            else:
                stop_reason = "slot_system_error"
            shortfall_reason = (
                f"{stop_reason}; fingerprint={enrich_stats.get('system_error_fingerprint')}; "
                f"{str(enrich_stats.get('system_error'))[:300]}"
            )
            live_trace.event(
                "decision",
                "Slot system error — replacements halted: "
                f"{enrich_stats.get('system_error_fingerprint')}: "
                f"{str(enrich_stats.get('system_error'))[:240]}",
                level="error",
                ready_count=ready_count,
                requested_count=requested,
                shortfall_count=shortfall,
                shortfall_reason=shortfall_reason,
                slots=[s.as_trace() for s in slots],
                **{k: v for k, v in enrich_stats.items() if k != "system_error"},
                system_error=str(enrich_stats.get("system_error"))[:500],
            )
        elif shortfall > 0:
            shortfall_reason = (
                f"outreach_ready_shortfall; ready={ready_count} requested={requested}; "
                f"replacement_rounds={replacement_round}/{replacement_max}; "
                f"exhausted={enrich_stats['exhausted']} technical={enrich_stats['technical_failure']}"
            )

        if stop_reason not in {"slot_system_error", "cost_budget_reached", "pricing_unknown"}:
            live_trace.event(
                "decision",
                "Outreach-ready summary: "
                f"ready={ready_count}/{requested}, researched={enrich_stats['companies_researched']}, "
                f"rejected={enrich_stats['rejected_during_slots']}, collisions={enrich_stats['duplicate_collisions']}, "
                f"replacements={enrich_stats['replacement_slots']}, shortfall={shortfall}",
                level="success" if shortfall == 0 else "warning",
                ready_count=ready_count,
                requested_count=requested,
                shortfall_count=shortfall,
                shortfall_reason=shortfall_reason,
                slots=[s.as_trace() for s in slots],
                **enrich_stats,
            )
        live_trace.event(
            "dedupe",
            f"Duplicate check summary: new={created}, updated={updated}, duplicates={duplicates}, excluded={excluded_n}",
            level="success",
            created=created,
            updated=updated,
            duplicates=duplicates,
            excluded=excluded_n,
        )
        live_trace.event(
            "filter",
            "Prospect enrichment completeness summary",
            level="info",
            **completeness,
            **enrich_stats,
        )
        live_trace.event(
            "score",
            "Component scoring summary",
            level="success",
            scores=score_rows[:25],
        )
        live_trace.event(
            "save",
            f"Records saved: created={created}, updated={updated}",
            level="success",
            created=created,
            updated=updated,
        )

        if stop_reason not in {"slot_system_error", "cost_budget_reached", "pricing_unknown"}:
            stop_reason = "completed"
            if ready_count == 0:
                stop_reason = "no_usable_prospects" if seeds else "no_prospects_returned"
            elif shortfall > 0:
                stop_reason = "completed_with_shortfall"

        return {
            "ok": stop_reason not in {"slot_system_error"},
            "stop_reason": stop_reason,
            "errors": errors,
            "requested_count": requested,
            "found_count": sum(1 for s in seeds if s),
            "candidates": sum(1 for s in seeds if s),
            "candidates_inspected": enrich_stats["companies_researched"],
            "created": created,
            "updated": updated,
            "duplicates": duplicates,
            "excluded": excluded_n,
            "usable_count": len(usable_results),
            "ready_count": ready_count,
            "results": results,
            "usable_results": usable_results,
            "cost_usd": round(cost_usd, 5),
            "budget": budget.snapshot(),
            "discovery_attempts": attempt_log,
            "search_attempts": len(attempt_log),
            "shortfall_count": shortfall,
            "shortfall_reason": shortfall_reason,
            "enrichment": enrich_stats,
            "completeness": completeness,
            "slots": [s.as_trace() for s in slots],
            "terminal": terminal,
            "replacement_rounds": replacement_round,
            "agentic": True,
            "skip_research": True,
        }

    def _build_reserved_domains(self, seeds: list[dict[str, Any] | None]) -> set[str]:
        reserved: set[str] = set()
        for s in seeds:
            if not s:
                continue
            d = (s.get("normalized_domain") or "").strip().lower()
            if d:
                reserved.add(d)
        # Permanent DB domains (do not rediscover).
        try:
            rows = self.db.query(Prospect.domain).filter(Prospect.domain.isnot(None)).limit(5000).all()
            for (domain,) in rows:
                nd = normalize_domain(domain) or (domain or "").strip().lower()
                if nd:
                    reserved.add(nd)
        except Exception:  # noqa: BLE001
            logger.exception("failed loading reserved domains from DB")
        return reserved

    def _run_slots_parallel(
        self,
        slots: list[ProspectSlot],
        *,
        admin: dict[str, Any],
        context: dict[str, Any],
        reserved: set[str],
        max_candidates: int,
        concurrency: int,
        max_retries: int,
        enrich_stats: dict[str, Any],
        budget: DiscoveryCostBudget | None = None,
        estimated_retry_usd: float = 0.05,
        reservations: dict[str, str] | None = None,
    ) -> None:
        active = {"n": 0, "peak": 0}
        lock = threading.Lock()
        reserved_snapshot = sorted(reserved)
        reservations = reservations or {}

        def job(slot: ProspectSlot) -> ProspectSlot:
            with lock:
                active["n"] += 1
                active["peak"] = max(active["peak"], active["n"])
            try:
                slot.status = SLOT_RESEARCHING
                enrich_stats["started"] = int(enrich_stats.get("started") or 0) + 1
                return self._run_one_slot(
                    slot,
                    admin=admin,
                    context=context,
                    reserved_domains=reserved_snapshot,
                    max_candidates=max_candidates,
                    max_retries=max_retries,
                    enrich_stats=enrich_stats,
                    budget=budget,
                    estimated_retry_usd=estimated_retry_usd,
                    initial_reservation_id=reservations.get(slot.slot_id),
                )
            finally:
                with lock:
                    active["n"] -= 1

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futs: list[tuple[ProspectSlot, Future]] = []
            for slot in slots:
                ctx = contextvars.copy_context()
                futs.append((slot, pool.submit(ctx.run, job, slot)))
            for slot, fut in futs:
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("slot worker crashed %s", slot.slot_id)
                    slot.status = SLOT_TECHNICAL_FAILURE
                    slot.failure_reason = str(exc)
                    enrich_stats["failed"] = int(enrich_stats.get("failed") or 0) + 1
        enrich_stats["observed_peak_concurrency"] = max(
            int(enrich_stats.get("observed_peak_concurrency") or 0), int(active["peak"])
        )

    def _run_one_slot(
        self,
        slot: ProspectSlot,
        *,
        admin: dict[str, Any],
        context: dict[str, Any],
        reserved_domains: list[str],
        max_candidates: int,
        max_retries: int,
        enrich_stats: dict[str, Any],
        budget: DiscoveryCostBudget | None = None,
        estimated_retry_usd: float = 0.05,
        initial_reservation_id: str | None = None,
    ) -> ProspectSlot:
        attempts_allowed = max(1, int(max_retries) + 1)
        repair_notes: list[str] = []
        last_error: str | None = None
        seed = slot.seed_company
        name = (seed or {}).get("company_name") or slot.slot_id
        open_reservation = initial_reservation_id

        for attempt in range(1, attempts_allowed + 1):
            if attempt > 1:
                if budget is not None:
                    if budget.pricing_unknown or not budget.can_launch(estimated_retry_usd):
                        slot.status = SLOT_TECHNICAL_FAILURE
                        slot.failure_reason = budget.stop_reason or "cost_budget_reached"
                        enrich_stats["technical_failure"] = int(
                            enrich_stats.get("technical_failure") or 0
                        ) + 1
                        live_trace.event(
                            "error",
                            (
                                "Actual cost unavailable — long-context pricing is not configured."
                                if budget.pricing_unknown
                                else f"Cost budget reached — skipping retry for {name}"
                            ),
                            level="error",
                            slot_id=slot.slot_id,
                            stop_reason=budget.stop_reason or "cost_budget_reached",
                            **budget.snapshot(),
                        )
                        return slot
                    open_reservation = budget.try_reserve(
                        estimated_retry_usd,
                        reservation_id=f"{slot.slot_id}-retry-{attempt}",
                    )
                    if open_reservation is None:
                        slot.status = SLOT_TECHNICAL_FAILURE
                        slot.failure_reason = budget.stop_reason or "cost_budget_reached"
                        return slot

            built = DiscoveryPromptBuilder.build_outreach_slot(
                admin,
                admin.get("score_weights") or {},
                slot_id=slot.slot_id,
                seed=seed,
                reserved_domains=reserved_domains,
                max_candidates=max_candidates,
                coverage_context=context,
                repair_notes=repair_notes or None,
            )
            live_trace.event(
                "research",
                f"Slot research started: {name}",
                level="info",
                slot_id=slot.slot_id,
                company=name,
                attempt=attempt,
                repair=bool(repair_notes),
                max_candidates=max_candidates,
            )
            answer = self.client.discover(
                instructions=built["instructions"],
                prompt=built["prompt"],
                call_type="outreach_slot" if attempt == 1 and slot.status != SLOT_COLLISION_RETRY else "slot_retry",
            )
            if budget is not None:
                budget.settle(
                    open_reservation,
                    answer.cost_usd,
                    cost_complete=bool(answer.cost_complete),
                    pricing_status=answer.pricing_status,
                )
                open_reservation = None
                if budget.pricing_unknown:
                    live_trace.event(
                        "error",
                        "Actual cost unavailable — long-context pricing is not configured.",
                        level="error",
                        slot_id=slot.slot_id,
                        stop_reason="pricing_unknown",
                        **budget.snapshot(),
                    )
            try:
                from acquisition.services.acquisition_usage_recorder import AcquisitionUsageRecorder

                AcquisitionUsageRecorder(self.db).record_call(
                    answer,
                    run_id=live_trace.current_run_id(),
                    call_type=answer.call_type or "outreach_slot",
                    attempt_number=attempt,
                    slot_id=slot.slot_id,
                    company=name if name != slot.slot_id else None,
                    domain=((seed or {}).get("normalized_domain") or None),
                )
            except Exception:  # noqa: BLE001
                logger.exception("usage record failed for slot %s", slot.slot_id)
            if answer.cost_complete and answer.cost_usd is not None:
                slot.cost_usd += float(answer.cost_usd)
            elif budget is not None and budget.pricing_unknown:
                # Keep any parsed success for this attempt but stop further paid calls.
                pass

            if answer.error:
                last_error = answer.error
                enrich_stats["transient_failures"] = int(enrich_stats.get("transient_failures") or 0) + 1
                kind = "transient"
            elif not answer.data:
                last_error = "empty_result"
                enrich_stats["transient_failures"] = int(enrich_stats.get("transient_failures") or 0) + 1
                kind = "transient"
            else:
                try:
                    parsed = parse_slot_result(
                        answer.data,
                        slot_id=slot.slot_id,
                        company_hint=(seed or {}).get("company_name"),
                    )
                except StructuredOutputValidationError as exc:
                    enrich_stats["schema_validation_failures"] = (
                        int(enrich_stats.get("schema_validation_failures") or 0) + 1
                    )
                    last_error = str(exc)
                    repair_notes.append(str(exc))
                    kind = "schema"
                    parsed = None
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    kind = "transient"
                    parsed = None

                if parsed is not None:
                    if parsed.get("parse_errors") and parsed.get("status") == "technical_failure":
                        enrich_stats["schema_validation_failures"] = (
                            int(enrich_stats.get("schema_validation_failures") or 0) + 1
                        )
                        msgs = [
                            e.get("message") or e.get("field") or "schema"
                            for e in (parsed.get("parse_errors") or [])
                        ]
                        last_error = "; ".join(str(m) for m in msgs)
                        repair_notes.append(last_error)
                        kind = "schema"
                    else:
                        slot.candidate_attempts = int(parsed.get("candidates_attempted") or 0)
                        slot.rejected_candidates = list(parsed.get("rejected_candidates") or [])
                        slot.ai_recommended_ready = bool(parsed.get("ai_recommended_ready"))
                        status = parsed.get("status")
                        if status == "ready" and parsed.get("prospect"):
                            slot.final_prospect = parsed["prospect"]
                            slot.status = SLOT_READY
                            slot.failure_reason = None
                            if attempt > 1:
                                enrich_stats["recovered_after_retry"] = (
                                    int(enrich_stats.get("recovered_after_retry") or 0) + 1
                                )
                            return slot
                        if status == "exhausted":
                            slot.status = SLOT_EXHAUSTED
                            slot.failure_reason = parsed.get("failure_reason") or "slot_exhausted"
                            return slot
                        last_error = parsed.get("failure_reason") or "technical_failure"
                        kind = "transient"

            if attempt < attempts_allowed:
                if budget is not None and budget.pricing_unknown:
                    if slot.status not in {SLOT_READY, SLOT_EXHAUSTED}:
                        slot.status = SLOT_TECHNICAL_FAILURE
                        slot.failure_reason = "pricing_unknown"
                    return slot
                enrich_stats["retries_attempted"] = int(enrich_stats.get("retries_attempted") or 0) + 1
                delay = min(8.0, (2 ** (attempt - 1)) * 0.25) + random.uniform(0, 0.15)
                live_trace.event(
                    "research",
                    f"Slot retry {attempt}/{max_retries} for {name}",
                    level="warning",
                    slot_id=slot.slot_id,
                    error=last_error,
                    backoff_seconds=round(delay, 3),
                    kind=kind if "kind" in locals() else "unknown",
                )
                if (getattr(self.settings, "environment", "") or "").lower() != "test":
                    time.sleep(delay)
                continue

            slot.status = SLOT_TECHNICAL_FAILURE
            slot.failure_reason = last_error or "technical_failure"
            enrich_stats["failed"] = int(enrich_stats.get("failed") or 0) + 1
            if budget is not None:
                budget.release_if_open(open_reservation)
            return slot
        if budget is not None:
            budget.release_if_open(open_reservation)
        slot.status = SLOT_TECHNICAL_FAILURE
        slot.failure_reason = last_error or "slot_failed"
        enrich_stats["failed"] = int(enrich_stats.get("failed") or 0) + 1
        return slot
