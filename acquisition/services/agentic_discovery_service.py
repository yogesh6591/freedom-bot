"""Agentic OpenAI discovery — Stage A company find + Stage B per-company enrichment.

Exa and NPPES are never called from this path.
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.openai_discovery_client import (
    OpenAIDiscoveryClient,
    resolve_openai_discovery_client,
)
from acquisition.models.prospect import AcquisitionStatus, ApprovalStatus, Prospect
from acquisition.services import live_trace
from acquisition.services.agentic_discovery_schema import (
    StructuredOutputValidationError,
    evaluate_outreach_ready,
    evaluate_readiness,
    has_decision_maker,
    has_legitimate_contact_path,
    has_verified_business_email,
    is_transient_enrichment_error,
    is_usable_prospect,
    matches_exclusion,
    parse_discovery_candidates,
    parse_discovery_result,
    parse_enrichment_result,
)
from acquisition.services.discovery_context_builder import DiscoveryContextBuilder
from acquisition.services.discovery_prompt_builder import DiscoveryPromptBuilder
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.outreach_slot_orchestrator import OutreachSlotOrchestrator
from acquisition.services.priority_score import (
    ScoringWeightsError,
    calculate_priority_score,
    validate_scoring_weights,
)
from acquisition.services.prospect_identity import (
    find_existing_company_or_prospect,
    is_placeholder_email,
    placeholder_email_for_domain,
)
from acquisition.services.prospect_research_service import ProspectResearchService

logger = logging.getLogger(__name__)

UNKNOWN_DM = "Unknown decision-maker"


class AgenticDiscoveryService:
    name = "openai_agentic_discovery"

    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        client: OpenAIDiscoveryClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.icp = IcpSettingsService(db)
        self.evidence = ProspectResearchService(db)
        self.client = client or resolve_openai_discovery_client(db, self.settings)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.client.configured,
            "provider_name": self.name,
            "message": self.client.status_message(),
            "model": self.client.model,
            "reasoning_effort": self.client.reasoning_effort,
        }

    def _resolve_max_discovery_attempts(self) -> int:
        override = getattr(self.settings, "discovery_max_attempts", None)
        if override is not None:
            return max(1, int(override))
        return max(1, int(getattr(self.settings, "openai_discovery_max_attempts", 3) or 3))

    def run_cycle(self, *, limit: int | None = None, preview: bool = False) -> dict[str, Any]:
        """Stage A discovery (with top-up) ∥ Stage B enrichment → backend score/save."""
        admin = self.icp.as_dict()
        requested = int(limit or admin.get("discovery_cycle_limit") or 5)
        requested = max(1, min(requested, int(self.settings.discovery_cycle_hard_max or 25)))
        max_attempts = self._resolve_max_discovery_attempts()
        concurrency = max(1, int(getattr(self.settings, "enrichment_max_concurrency", 5) or 5))
        max_retries = max(0, int(getattr(self.settings, "enrichment_max_retries", 1) or 0))

        live_trace.event("run", "Run started", level="info", requested=requested, provider=self.name)
        live_trace.event(
            "icp",
            "Admin/ICP loaded",
            level="success",
            priority_categories=admin.get("priority_categories") or [],
            excluded_categories=admin.get("excluded_categories") or [],
            geography=admin.get("geography") or {},
            revenue=admin.get("revenue") or {},
            score_weights=admin.get("score_weights") or {},
        )

        try:
            fit_w, timing_w, reach_w = validate_scoring_weights(admin.get("score_weights"))
        except ScoringWeightsError as exc:
            live_trace.event("error", str(exc), level="error")
            return self._fail(requested, "scoring_weights_invalid", [str(exc)])

        if not self.client.configured:
            msg = "OpenAI discovery provider is not configured"
            live_trace.event("error", msg, level="error")
            return self._fail(requested, "provider_not_configured", [msg])

        context = DiscoveryContextBuilder(self.db).build(admin)
        live_trace.event(
            "plan",
            "Previous discovery context loaded",
            level="success",
            total_discovered=context.get("total_discovered_prospects"),
            category_coverage=context.get("category_coverage"),
            state_coverage=context.get("state_coverage"),
            max_discovery_attempts=max_attempts,
            enrichment_max_concurrency=concurrency,
        )

        if preview:
            candidates, attempt_log, cost_usd, errors = self._discover_with_topup(
                admin=admin,
                context=context,
                requested=requested,
                max_attempts=max_attempts,
            )
            live_trace.event(
                "decision",
                "Preview complete — no prospects were saved",
                level="success",
                selected=[
                    {"company": c["company_name"], "domain": c.get("normalized_domain")}
                    for c in candidates[:20]
                ],
            )
            return {
                "ok": True,
                "preview": True,
                "provider": self.status(),
                "limit": requested,
                "candidates": len(candidates),
                "created": 0,
                "updated": 0,
                "duplicates": 0,
                "usable_count": 0,
                "errors": errors,
                "results": [],
                "usable_results": [],
                "returned_prospects": candidates,
                "stop_reason": "preview_complete",
                "cost_usd": cost_usd,
                "agentic": True,
                "skip_research": True,
                "discovery_attempts": attempt_log,
                "requested_count": requested,
                "found_count": len(candidates),
                "shortfall_count": max(0, requested - len(candidates)),
            }

        live_trace.event(
            "run",
            f"Requested FINAL outreach-ready: {requested}",
            level="info",
            requested_final_outreach_ready=requested,
        )

        orch = OutreachSlotOrchestrator(
            db=self.db,
            settings=self.settings,
            client=self.client,
            persist_one=self._persist_one,
            discover_with_topup=self._discover_with_topup,
        )
        out = orch.run(
            admin=admin,
            context=context,
            requested=requested,
            max_attempts=max_attempts,
            concurrency=concurrency,
            max_retries=max_retries,
            fit_w=fit_w,
            timing_w=timing_w,
            reach_w=reach_w,
        )
        coverage = self._update_coverage(
            context,
            created=int(out.get("created") or 0),
            duplicates=int(out.get("duplicates") or 0),
            returned=int(out.get("found_count") or 0),
        )
        out["coverage"] = coverage
        out["provider"] = self.status()
        out["limit"] = requested
        try:
            from acquisition.services.acquisition_usage_recorder import AcquisitionUsageRecorder

            usage = AcquisitionUsageRecorder(self.db).run_totals(live_trace.current_run_id())
            ready_n = int(out.get("ready_count") or 0)
            usage["cost_per_ready_usd"] = AcquisitionUsageRecorder.cost_per_ready(
                usage.get("total_cost_usd"), ready_n
            )
            out["ai_usage"] = usage
            if not usage.get("cost_complete", True):
                live_trace.event(
                    "error",
                    "Actual cost unavailable — long-context pricing is not configured.",
                    level="error",
                    stop_reason="pricing_unknown",
                    **{k: usage[k] for k in usage if k != "per_call"},
                )
            live_trace.event(
                "score",
                "Actual OpenAI usage & cost",
                level="info",
                **{k: usage[k] for k in usage if k != "per_call"},
                per_call=usage.get("per_call") or [],
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to roll up acquisition AI usage")
        live_trace.event(
            "save",
            f"Agentic discovery phase finished — ready={out.get('ready_count')} "
            f"usable={out.get('usable_count')}",
            level="success" if not out.get("shortfall_count") else "warning",
            usable=out.get("usable_count"),
            ready=out.get("ready_count"),
            coverage=coverage,
            requested_count=requested,
            found_count=out.get("found_count"),
            shortfall_count=out.get("shortfall_count"),
            shortfall_reason=out.get("shortfall_reason"),
            search_attempts=out.get("search_attempts"),
            enrichment=out.get("enrichment"),
            slots=out.get("slots"),
            ai_usage=out.get("ai_usage"),
        )
        return out

    def _fail(self, requested: int, reason: str, errors: list[str]) -> dict[str, Any]:
        return {
            "ok": False,
            "provider": self.status(),
            "limit": requested,
            "errors": errors,
            "stop_reason": reason,
            "created": 0,
            "updated": 0,
            "duplicates": 0,
            "usable_count": 0,
            "ready_count": 0,
            "candidates": 0,
            "results": [],
            "usable_results": [],
            "agentic": True,
            "requested_count": requested,
            "found_count": 0,
            "shortfall_count": requested,
            "shortfall_reason": reason,
            "search_attempts": 0,
        }

    def _discover_with_topup(
        self,
        *,
        admin: dict[str, Any],
        context: dict[str, Any],
        requested: int,
        max_attempts: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, list[str]]:
        """Preview / sequential Stage A only (no enrichment)."""
        collected: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        attempt_log: list[dict[str, Any]] = []
        cost_usd = 0.0
        errors: list[str] = []

        for attempt in range(1, max_attempts + 1):
            remaining = requested - len(collected)
            if remaining <= 0:
                break
            batch, added, cost, err, log_entry = self._run_discovery_attempt(
                admin=admin,
                context=context,
                remaining=remaining,
                collected=collected,
                attempt=attempt,
                max_attempts=max_attempts,
                seen_keys=seen_keys,
                requested=requested,
            )
            cost_usd += cost
            if err:
                errors.append(err)
            attempt_log.append(log_entry)
            collected.extend(batch)
            if added == 0:
                break

        shortfall = max(0, requested - len(collected))
        # Aggregate Stage A meta across attempts (only when structured AI provided it).
        considered_vals = [
            int(a["companies_considered"])
            for a in attempt_log
            if a.get("companies_considered") is not None
        ]
        rejected_n = sum(int(a.get("rejected_before_selection") or 0) for a in attempt_log)
        reason_counts: dict[str, int] = {}
        for a in attempt_log:
            for k, v in (a.get("rejection_reason_counts") or {}).items():
                try:
                    reason_counts[str(k)] = reason_counts.get(str(k), 0) + int(v)
                except (TypeError, ValueError):
                    continue
        live_trace.event(
            "plan",
            "MASTER DISCOVERY complete: "
            f"strong seeds {len(collected)}/{requested}"
            + (f" (shortfall {shortfall})" if shortfall else ""),
            level="success" if not shortfall else "warning",
            requested_count=requested,
            found_count=len(collected),
            strong_seeds_returned=len(collected),
            shortfall_count=shortfall,
            search_attempts=len(attempt_log),
            attempts=attempt_log,
            companies_considered=sum(considered_vals) if considered_vals else None,
            rejected_before_selection=rejected_n or None,
            rejection_reason_counts=reason_counts or None,
        )
        return collected[:requested], attempt_log, cost_usd, errors

    def _discover_and_enrich_parallel(
        self,
        *,
        admin: dict[str, Any],
        context: dict[str, Any],
        requested: int,
        max_attempts: int,
        concurrency: int,
        max_retries: int,
        seen_keys: set[str] | None = None,
        already_collected: list[dict[str, Any]] | None = None,
        phase_label: str = "initial",
    ) -> tuple[
        list[dict[str, Any]],
        list[tuple[dict[str, Any], dict[str, Any] | None]],
        list[dict[str, Any]],
        float,
        list[str],
        dict[str, Any],
    ]:
        """Overlap Stage A top-up with Stage B enrichment under a concurrency cap.

        Discovery attempts run on the calling thread. Enrichment runs in a
        ThreadPoolExecutor (OpenAI client only — no DB session in workers).

        Returns enriched_pairs as (candidate, result_dict|None) where result_dict is:
        {"row": ..., "error": ..., "terminal": ..., "meta": ...}
        """
        collected: list[dict[str, Any]] = []
        keys = seen_keys if seen_keys is not None else set()
        prior = list(already_collected or [])
        attempt_log: list[dict[str, Any]] = []
        cost_usd = 0.0
        errors: list[str] = []
        enrich_meta = {
            "started": 0,
            "completed": 0,
            "failed": 0,
            "schema_validation_failures": 0,
            "transient_failures": 0,
            "retries_attempted": 0,
            "recovered_after_retry": 0,
            "observed_peak_concurrency": 0,
        }
        active_lock = threading.Lock()
        active_count = {"n": 0, "peak": 0}
        futures: list[tuple[dict[str, Any], Future]] = []

        def _enrich_job(candidate: dict[str, Any]) -> dict[str, Any]:
            with active_lock:
                active_count["n"] += 1
                if active_count["n"] > active_count["peak"]:
                    active_count["peak"] = active_count["n"]
            try:
                return self._enrich_one_with_retries(admin, candidate, max_retries=max_retries)
            finally:
                with active_lock:
                    active_count["n"] -= 1

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for attempt in range(1, max_attempts + 1):
                remaining = requested - len(collected)
                if remaining <= 0:
                    break
                batch, added, cost, err, log_entry = self._run_discovery_attempt(
                    admin=admin,
                    context=context,
                    remaining=remaining,
                    collected=prior + collected,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    seen_keys=keys,
                    requested=requested,
                    phase_label=phase_label,
                )
                cost_usd += cost
                if err:
                    errors.append(err)
                attempt_log.append(log_entry)
                collected.extend(batch)

                if batch:
                    remaining_after = max(0, requested - len(collected))
                    topup_pending = remaining_after > 0 and attempt < max_attempts and added > 0
                    live_trace.event(
                        "research",
                        "Parallel work started: "
                        f"{len(batch)} enrichment job(s)"
                        + (f", 1 top-up discovery job" if topup_pending else ""),
                        level="info",
                        enrichment_jobs=len(batch),
                        topup_jobs=1 if topup_pending else 0,
                        pool_size=len(collected),
                        remaining=remaining_after,
                        concurrency=concurrency,
                        phase=phase_label,
                    )
                    for cand in batch:
                        enrich_meta["started"] += 1
                        job_ctx = contextvars.copy_context()
                        fut = pool.submit(job_ctx.run, _enrich_job, cand)
                        futures.append((cand, fut))

                if added == 0:
                    break

            enriched_pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
            for cand, fut in futures:
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("enrichment worker crashed for %s", cand.get("company_name"))
                    enrich_meta["failed"] += 1
                    errors.append(f"{cand.get('company_name')}: enrichment {exc}")
                    live_trace.event(
                        "error",
                        f"Enrichment failed for {cand.get('company_name')}: {exc}",
                        level="error",
                        company=cand.get("company_name"),
                    )
                    enriched_pairs.append(
                        (
                            cand,
                            {
                                "row": None,
                                "error": str(exc),
                                "terminal": "technical_failure",
                                "meta": {},
                            },
                        )
                    )
                    continue

                cost_usd += float(result.get("cost_usd") or 0.0)
                meta = result.get("meta") or {}
                for k in (
                    "schema_validation_failures",
                    "transient_failures",
                    "retries_attempted",
                    "recovered_after_retry",
                ):
                    enrich_meta[k] = int(enrich_meta.get(k) or 0) + int(meta.get(k) or 0)

                if result.get("row") is not None:
                    enrich_meta["completed"] += 1
                    enriched_pairs.append((cand, result))
                else:
                    enrich_meta["failed"] += 1
                    err_msg = result.get("error") or "enrichment empty"
                    errors.append(f"{cand.get('company_name')}: {err_msg}")
                    enriched_pairs.append((cand, result))

        enrich_meta["observed_peak_concurrency"] = int(active_count["peak"])
        shortfall = max(0, requested - len(collected))
        live_trace.event(
            "plan",
            f"Discovery complete ({phase_label}): found {len(collected)}/{requested}"
            + (f" (shortfall {shortfall})" if shortfall else ""),
            level="success" if not shortfall else "warning",
            requested_count=requested,
            found_count=len(collected),
            shortfall_count=shortfall,
            search_attempts=len(attempt_log),
            attempts=attempt_log,
            phase=phase_label,
        )
        live_trace.event(
            "research",
            "Enrichment summary: "
            f"started={enrich_meta['started']}, completed={enrich_meta['completed']}, "
            f"failed={enrich_meta['failed']}, schema_failures={enrich_meta['schema_validation_failures']}, "
            f"transient={enrich_meta['transient_failures']}, retries={enrich_meta['retries_attempted']}, "
            f"recovered={enrich_meta['recovered_after_retry']}",
            level="success" if enrich_meta["failed"] == 0 else "warning",
            **enrich_meta,
            concurrency=concurrency,
            phase=phase_label,
        )
        return collected[:requested], enriched_pairs, attempt_log, cost_usd, errors, enrich_meta

    def _run_discovery_attempt(
        self,
        *,
        admin: dict[str, Any],
        context: dict[str, Any],
        remaining: int,
        collected: list[dict[str, Any]],
        attempt: int,
        max_attempts: int,
        seen_keys: set[str],
        requested: int,
        phase_label: str = "initial",
    ) -> tuple[list[dict[str, Any]], int, float, str | None, dict[str, Any]]:
        already = [
            {
                "company_name": c["company_name"],
                "domain": c.get("normalized_domain") or "",
            }
            for c in collected
        ]
        reserved = list(context.get("do_not_rediscover_domains") or context.get("known_domains") or [])
        for item in already:
            if item.get("domain"):
                reserved.append(str(item["domain"]))
        built = DiscoveryPromptBuilder.build_discovery(
            admin,
            context,
            remaining_count=remaining,
            already_found=already,
            attempt=attempt,
            max_attempts=max_attempts,
            reserved_domains=reserved,
            scoring_settings=admin.get("score_weights") or {},
        )
        live_trace.event(
            "harvest",
            f"MASTER DISCOVERY — REQUEST attempt {attempt}/{max_attempts} "
            f"({phase_label}, need {remaining} strong seeds)",
            level="info",
            source="openai_web",
            model=self.client.model,
            fetch_limit=remaining,
            attempt=attempt,
            remaining=remaining,
            phase=phase_label,
            prompt=built.get("prompt"),
        )
        live_trace.event(
            "research",
            f"Master discovery web research started (attempt {attempt}, {phase_label})",
            level="info",
            model=self.client.model,
            source="openai_web",
            attempt=attempt,
            phase=phase_label,
        )
        answer = self.client.discover(
            instructions=built["instructions"],
            prompt=built["prompt"],
            call_type="master_discovery",
        )
        try:
            from acquisition.services.acquisition_usage_recorder import AcquisitionUsageRecorder
            from acquisition.services import live_trace as _lt

            AcquisitionUsageRecorder(self.db).record_call(
                answer,
                run_id=_lt.current_run_id(),
                call_type="master_discovery",
                attempt_number=attempt,
            )
        except Exception:  # noqa: BLE001
            logger.exception("usage record failed for master discovery")
        cost = float(answer.cost_usd) if answer.cost_complete and answer.cost_usd is not None else 0.0
        log_pricing = {
            "pricing_status": answer.pricing_status,
            "cost_complete": bool(answer.cost_complete),
            "cost_usd": answer.cost_usd,
        }
        if answer.error or not answer.data:
            err = answer.error or "openai_discovery_empty"
            live_trace.event(
                "harvest",
                f"MASTER DISCOVERY attempt {attempt} returned 0 (error: {err})",
                level="error",
                source="openai_web",
                attempt=attempt,
                error=err,
                phase=phase_label,
                **log_pricing,
            )
            return (
                [],
                0,
                cost,
                f"attempt_{attempt}:{err}",
                {
                    "attempt": attempt,
                    "requested": remaining,
                    "returned": 0,
                    "error": err,
                    "phase": phase_label,
                    **log_pricing,
                },
            )

        live_trace.event(
            "research",
            f"Master discovery research completed (attempt {attempt})",
            level="success",
            search_calls=answer.search_calls,
            source="openai_web",
            attempt=attempt,
            phase=phase_label,
            **log_pricing,
        )
        parsed = parse_discovery_result(answer.data)
        batch = parsed.get("prospects") or []
        meta = parsed.get("discovery_meta") or {}
        accepted: list[dict[str, Any]] = []
        for row in batch:
            key = (
                (row.get("normalized_domain") or "").strip().lower()
                or (row.get("company_name") or "").strip().lower()
            )
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            accepted.append(row)
            if len(accepted) >= remaining:
                break

        log_entry = {
            "attempt": attempt,
            "requested": remaining,
            "returned": len(batch),
            "accepted": len(accepted),
            "search_calls": answer.search_calls,
            "phase": phase_label,
            "companies_considered": meta.get("companies_considered"),
            "rejected_before_selection": len(meta.get("rejected_before_selection") or []),
            "rejection_reason_counts": meta.get("rejection_reason_counts") or {},
            "pricing_status": answer.pricing_status,
            "cost_complete": bool(answer.cost_complete),
            "cost_usd": answer.cost_usd,
        }
        live_trace.event(
            "harvest",
            f"MASTER DISCOVERY attempt {attempt}: strong seeds {len(accepted)}/{remaining}"
            + (
                f"; considered={meta.get('companies_considered')}"
                if meta.get("companies_considered") is not None
                else ""
            )
            + (
                f"; rejected_before_selection={len(meta.get('rejected_before_selection') or [])}"
                if meta.get("rejected_before_selection")
                else ""
            ),
            level="success" if accepted else "warning",
            source="openai_web",
            attempt=attempt,
            returned=len(batch),
            accepted=len(accepted),
            pool_size=len(accepted),
            requested_total=requested,
            remaining_after=max(0, remaining - len(accepted)),
            phase=phase_label,
            companies_considered=meta.get("companies_considered"),
            rejected_before_selection=len(meta.get("rejected_before_selection") or []) or None,
            rejection_reason_counts=meta.get("rejection_reason_counts") or None,
            rejected_samples=(meta.get("rejected_before_selection") or [])[:12],
            returned_companies=[
                {
                    "company": c.get("company_name"),
                    "domain": c.get("normalized_domain"),
                    "city": (c.get("location") or {}).get("city"),
                    "state": (c.get("location") or {}).get("state"),
                    "why_strong_seed": c.get("why_strong_seed"),
                    "source": "openai_web",
                }
                for c in batch[:40]
            ],
        )
        return accepted, len(accepted), cost, None, log_entry

    def _enrich_one_with_retries(
        self,
        admin: dict[str, Any],
        candidate: dict[str, Any],
        *,
        max_retries: int,
    ) -> dict[str, Any]:
        """Bounded retries: schema repair + transient backoff. Never raises TypeError out."""
        attempts_allowed = max(1, int(max_retries) + 1)
        total_cost = 0.0
        last_error: str | None = None
        name = candidate.get("company_name") or "unknown"
        repair_notes: list[str] = []
        meta = {
            "schema_validation_failures": 0,
            "transient_failures": 0,
            "retries_attempted": 0,
            "recovered_after_retry": 0,
        }
        for attempt in range(1, attempts_allowed + 1):
            try:
                row, cost, attempt_err, attempt_kind = self._enrich_one(
                    admin, candidate, repair_notes=repair_notes
                )
                total_cost += float(cost or 0.0)
                if row is not None:
                    if attempt > 1:
                        meta["recovered_after_retry"] += 1
                    return {
                        "row": row,
                        "error": None,
                        "terminal": "enriched",
                        "cost_usd": total_cost,
                        "meta": meta,
                    }
                last_error = attempt_err or "empty_result"
                if attempt_kind == "schema":
                    meta["schema_validation_failures"] += 1
                    if attempt_err:
                        repair_notes.append(attempt_err)
                elif attempt_kind == "transient" or is_transient_enrichment_error(last_error):
                    meta["transient_failures"] += 1
                else:
                    meta["transient_failures"] += 1
            except StructuredOutputValidationError as exc:
                meta["schema_validation_failures"] += 1
                last_error = str(exc)
                repair_notes.append(
                    f"{exc.field} expected {exc.expected} but received {exc.received_type}"
                )
                live_trace.event(
                    "research",
                    f"Schema validation failure for {name}: {exc.field}",
                    level="warning",
                    company=name,
                    validation=exc.as_dict(),
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                meta["transient_failures"] += 1
                logger.exception("enrichment failed for %s (attempt %s)", name, attempt)

            if attempt < attempts_allowed:
                meta["retries_attempted"] += 1
                # Exponential backoff + jitter for transient / repair retries.
                delay = min(8.0, (2 ** (attempt - 1)) * 0.25) + random.uniform(0, 0.15)
                live_trace.event(
                    "research",
                    f"Enrichment retry {attempt}/{max_retries} for {name}",
                    level="warning",
                    company=name,
                    attempt=attempt,
                    max_retries=max_retries,
                    error=last_error,
                    backoff_seconds=round(delay, 3),
                    repair_notes=repair_notes[-3:],
                )
                if (getattr(self.settings, "environment", "") or "").lower() != "test":
                    time.sleep(delay)

        live_trace.event(
            "error",
            f"Enrichment failed for {name}: {last_error}",
            level="error",
            company=name,
            terminal="technical_failure",
        )
        return {
            "row": None,
            "error": last_error or "enrichment_failed",
            "terminal": "technical_failure",
            "cost_usd": total_cost,
            "meta": meta,
        }

    def _enrich_one(
        self,
        admin: dict[str, Any],
        candidate: dict[str, Any],
        *,
        repair_notes: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, float, str | None, str]:
        """One enrichment OpenAI call + strict parse.

        Returns (row|None, cost, error|None, kind) where kind is
        ok|schema|transient|empty.
        """
        built = DiscoveryPromptBuilder.build_enrichment(
            admin, admin.get("score_weights") or {}, candidate
        )
        prompt = built["prompt"]
        if repair_notes:
            prompt = (
                prompt
                + "\n\nREPAIR INSTRUCTION\n"
                "The previous structured response was invalid:\n- "
                + "\n- ".join(repair_notes[-8:])
                + "\nReturn the COMPLETE enrichment result again and conform exactly to the "
                "provided schema. Scores must be integers 0-100 (never arrays). "
                "research_completeness.source_urls_consulted must be an integer count "
                "(never a list of URLs)."
            )
        live_trace.event(
            "research",
            f"Enrichment started: {candidate.get('company_name')}",
            level="info",
            company=candidate.get("company_name"),
            domain=candidate.get("normalized_domain"),
            model=self.client.model,
            repair=bool(repair_notes),
        )
        answer = self.client.discover(instructions=built["instructions"], prompt=prompt)
        cost = float(answer.cost_usd or 0.0)
        if answer.error:
            kind = "transient" if is_transient_enrichment_error(answer.error) else "transient"
            live_trace.event(
                "error",
                f"Enrichment failed for {candidate.get('company_name')}: {answer.error}",
                level="error",
                company=candidate.get("company_name"),
                error=answer.error,
                transient=True,
            )
            return None, cost, answer.error, kind
        if not answer.data:
            return None, cost, "empty_result", "transient"

        parsed = parse_enrichment_result(
            answer.data, company_hint=candidate.get("company_name")
        )
        if parsed.errors and not parsed.row:
            # Important/score schema failures → repair retry
            msgs = [e.get("message") or e.get("field") or "schema_error" for e in parsed.errors]
            err = "; ".join(str(m) for m in msgs)
            live_trace.event(
                "research",
                f"Enrichment schema invalid for {candidate.get('company_name')}",
                level="warning",
                company=candidate.get("company_name"),
                validation_errors=parsed.errors,
            )
            return None, cost, err, "schema"
        if not parsed.row:
            return None, cost, "empty_result", "empty"

        row = parsed.row
        if parsed.warnings:
            live_trace.event(
                "research",
                f"Enrichment validation warnings for {row.get('company_name')}",
                level="info",
                company=row.get("company_name"),
                validation_warnings=parsed.warnings[:12],
            )
        # Preserve Stage A identity if enrichment omits it.
        row["company_name"] = row.get("company_name") or candidate.get("company_name")
        row["website"] = row.get("website") or candidate.get("website")
        row["normalized_domain"] = row.get("normalized_domain") or candidate.get("normalized_domain")
        if not row.get("location") or not any((row.get("location") or {}).values()):
            row["location"] = candidate.get("location") or row.get("location")
        existing_urls = {
            (s.get("url") or "").strip().lower() for s in (row.get("sources") or []) if isinstance(s, dict)
        }
        for url in candidate.get("initial_evidence_urls") or []:
            u = str(url).strip()
            if u and u.lower() not in existing_urls:
                row.setdefault("sources", []).append(
                    {"url": u, "type": "discovery", "supports": ["company_identity"]}
                )
                existing_urls.add(u.lower())
        for src in candidate.get("sources") or []:
            if not isinstance(src, dict):
                continue
            u = (src.get("url") or "").strip()
            if u and u.lower() not in existing_urls:
                row.setdefault("sources", []).append(src)
                existing_urls.add(u.lower())
        return row, cost, None, "ok"

    def _persist_one(
        self,
        row: dict[str, Any],
        *,
        fit_w: float,
        timing_w: float,
        reach_w: float,
        ready_min_priority: float,
        ready_min_reach: float,
        force_ready: bool = False,
        ready_reason: str | None = None,
        admin: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        domain = row.get("normalized_domain")
        company = row["company_name"]
        existing = find_existing_company_or_prospect(
            self.db,
            company=company,
            domain=domain,
            website=row.get("website"),
        )
        created = existing is None
        duplicate = False
        if existing is not None and existing.discovery_source:
            prospect = existing
            duplicate = True
            created = False
        elif existing is not None:
            prospect = existing
            created = False
        else:
            prospect = Prospect(
                company=company.strip(),
                executive_name=UNKNOWN_DM,
                email=placeholder_email_for_domain(domain),
                email_confidence="placeholder",
                source="autonomous_discovery",
                approval_status=ApprovalStatus.PENDING,
                acquisition_status=AcquisitionStatus.DISCOVERED,
            )
            self.db.add(prospect)

        loc = row.get("location") or {}
        dm = row.get("decision_maker") or {}
        contact = row.get("contact") or {}
        revenue = row.get("revenue") or {}
        headcount = row.get("headcount") or {}
        completeness = row.get("research_completeness") or {}

        prospect.website = row.get("website") or prospect.website
        prospect.domain = domain or prospect.domain
        prospect.city = loc.get("city") or prospect.city
        prospect.state = loc.get("state") or prospect.state
        prospect.industry = row.get("industry") or prospect.industry
        prospect.business_type = row.get("business_type") or prospect.business_type
        prospect.company_description = row.get("description") or prospect.company_description
        if revenue.get("value_or_band"):
            status = (revenue.get("status") or "unknown").lower()
            band = str(revenue["value_or_band"])
            prospect.revenue_band = band if status == "verified" else f"{band} ({status})"
        if headcount.get("value_or_band"):
            status = (headcount.get("status") or "unknown").lower()
            band = str(headcount["value_or_band"])
            prospect.headcount_band = band if status == "verified" else f"{band} ({status})"

        dm_name = (dm.get("name") or "").strip()
        if dm_name:
            prospect.executive_name = dm_name
            prospect.executive_title = (dm.get("title") or "").strip() or prospect.executive_title
            authority = (dm.get("authority_level") or dm.get("relationship") or "").strip().lower()
            prospect.authority_type = authority or prospect.authority_type
            prospect.is_founder_or_equivalent = any(
                k in authority for k in ("founder", "owner", "ceo", "principal", "partner")
            ) or None
            if dm.get("profile_url"):
                prospect.linkedin_url = str(dm["profile_url"])
        else:
            # Explicit unknown — do not leave a fabricated name.
            if not prospect.executive_name or prospect.executive_name == UNKNOWN_DM:
                prospect.executive_name = UNKNOWN_DM

        email = (contact.get("business_email") or "").strip() or None
        if email and "@" in email:
            prospect.email = email
            prospect.email_confidence = (
                contact.get("email_status") or "unverified"
            ).strip() or "unverified"
        elif not prospect.email:
            prospect.email = placeholder_email_for_domain(domain)
            prospect.email_confidence = "placeholder"

        if contact.get("phone"):
            prospect.company_phone = str(contact["phone"])
        if contact.get("contact_url"):
            prospect.other_contact_path = str(contact["contact_url"])

        signals = []
        for s in row.get("timing_signals") or []:
            text = s.get("signal") or s.get("text")
            if text:
                signals.append(
                    {
                        "text": text,
                        "source_url": s.get("source_url"),
                        "date": s.get("date"),
                        "category": "timing",
                    }
                )
        if signals:
            prospect.public_signals = signals
        if row.get("research_notes"):
            prospect.research_notes = str(row["research_notes"])

        fit = float(row["fit_score"])
        timing = float(row["timing_score"])
        reach = float(row["reachability_score"])
        priority = calculate_priority_score(fit, timing, reach, fit_w, timing_w, reach_w)
        prospect.fit_score = fit
        prospect.fit_reasons = [row.get("fit_reason") or "AI fit assessment"]
        prospect.timing_score = timing
        prospect.timing_reasons = [row.get("timing_reason") or "AI timing assessment"]
        prospect.reachability_score = reach
        prospect.reachability_reasons = [
            row.get("reachability_reason") or "AI reachability assessment"
        ]
        prospect.priority_score = priority
        if force_ready:
            # Orchestrator already ran evaluate_outreach_ready with full Admin context.
            status = AcquisitionStatus.READY_FOR_OUTREACH
            readiness_reason = ready_reason or "Backend-validated ready for outreach"
        else:
            outreach_gate = evaluate_outreach_ready(
                row,
                admin,
                priority=priority,
                fit_w=fit_w,
                timing_w=timing_w,
                reach_w=reach_w,
            )
            status, readiness_reason = evaluate_readiness(
                priority=priority,
                fit=fit,
                reach=reach,
                prospect_row=row,
                ready_min_priority=ready_min_priority,
                ready_min_reach=ready_min_reach,
                phone=prospect.company_phone,
                contact_url=prospect.other_contact_path,
            )
            if status == AcquisitionStatus.READY_FOR_OUTREACH and not outreach_gate.get("ready"):
                status = AcquisitionStatus.SCORED
                readiness_reason = outreach_gate.get("reason_text") or readiness_reason
            elif outreach_gate.get("ready"):
                status = AcquisitionStatus.READY_FOR_OUTREACH
                readiness_reason = outreach_gate.get("reason_text") or readiness_reason
            # Honor explicit Admin threshold rejection even if legacy readiness says ready.
            if priority < ready_min_priority or reach < ready_min_reach:
                if status == AcquisitionStatus.READY_FOR_OUTREACH:
                    status = AcquisitionStatus.SCORED
                    readiness_reason = (
                        f"Priority {priority:.0f} / reachability {reach:.0f} below Admin ready "
                        f"thresholds ({ready_min_priority:.0f}/{ready_min_reach:.0f})"
                    )
        prospect.priority_reasons = [
            f"Weighted blend fit={fit:.0f} timing={timing:.0f} reachability={reach:.0f}",
            readiness_reason,
        ]
        prospect.scored_at = now
        prospect.discovery_source = "openai_agentic"
        prospect.last_researched_at = now
        if not prospect.first_seen_at:
            prospect.first_seen_at = now
        prospect.last_seen_at = now
        prospect.times_seen = int(prospect.times_seen or 0) + 1
        if not prospect.discovered_at:
            prospect.discovered_at = now
        prospect.website_verified = bool(prospect.website)
        prospect.acquisition_status = status

        # Persist research provenance + completeness (not just provider label).
        classif = dict(prospect.classification_json or {})
        classif["discovery_provider"] = "openai_agentic"
        classif["agentic_research"] = {
            "decision_maker_search_attempted": bool(dm.get("search_attempted", True)),
            "decision_maker_sources": list(dm.get("source_urls") or []),
            "decision_maker_confidence": dm.get("confidence") or "unknown",
            "contact_sources": list(contact.get("source_urls") or []),
            "research_completeness": completeness,
            "readiness_reason": readiness_reason,
            "source_url_count": len(row.get("sources") or []),
        }
        prospect.classification_json = classif

        self.db.commit()
        self.db.refresh(prospect)
        self._persist_sources(prospect, row)

        usable_gate = is_usable_prospect(row)
        usable = (not duplicate) and bool(usable_gate.get("usable"))
        return {
            "prospect_id": str(prospect.id),
            "created": created and not duplicate,
            "duplicate": duplicate,
            "company": prospect.company,
            "domain": prospect.domain,
            "priority_score": prospect.priority_score,
            "fit_score": prospect.fit_score,
            "timing_score": prospect.timing_score,
            "reachability_score": prospect.reachability_score,
            "acquisition_status": status.value,
            "readiness_reason": readiness_reason,
            "usable": usable,
            "usable_reason_code": usable_gate.get("reason_code"),
            "usable_reason_text": usable_gate.get("reason_text"),
            "already_researched": True,
            "decision_maker_found": has_decision_maker(row),
            "contact_ok": has_legitimate_contact_path(row)
            or bool(prospect.company_phone or prospect.other_contact_path),
            "business_email_ok": has_verified_business_email(row),
        }

    def _persist_sources(self, prospect: Prospect, row: dict[str, Any]) -> None:
        for src in row.get("sources") or []:
            url = (src.get("url") or "").strip()
            if not url:
                continue
            supports = src.get("supports") or []
            category = "research"
            if isinstance(supports, list) and supports:
                category = str(supports[0])
            self.evidence.add_or_update_source(
                prospect.id,
                source_url=url,
                source_type=str(src.get("type") or "other"),
                category=category,
                note=f"Agentic research evidence for {prospect.company}",
                confidence=0.75,
            )
        # Field-level DM / contact source URLs
        dm = row.get("decision_maker") or {}
        for url in dm.get("source_urls") or []:
            u = str(url).strip()
            if not u:
                continue
            self.evidence.add_or_update_source(
                prospect.id,
                source_url=u,
                source_type="decision_maker",
                category="decision_maker",
                note=f"Decision-maker evidence (confidence={dm.get('confidence') or 'unknown'})",
                confidence=0.8,
            )
        contact = row.get("contact") or {}
        for url in contact.get("source_urls") or []:
            u = str(url).strip()
            if not u:
                continue
            self.evidence.add_or_update_source(
                prospect.id,
                source_url=u,
                source_type="contact",
                category="contact",
                note="Contact path evidence",
                confidence=0.8,
            )
        self.db.commit()

    def _update_coverage(
        self,
        prior: dict[str, Any],
        *,
        created: int,
        duplicates: int,
        returned: int,
    ) -> dict[str, Any]:
        fresh = DiscoveryContextBuilder(self.db).build()
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_run": {
                "returned": returned,
                "newly_created": created,
                "duplicates": duplicates,
                "date": datetime.now(timezone.utc).date().isoformat(),
            },
            "category_coverage": fresh.get("category_coverage") or {},
            "state_coverage": fresh.get("state_coverage") or {},
            "commonly_missing_fields": fresh.get("commonly_missing_fields") or {},
            "total_discovered_prospects": fresh.get("total_discovered_prospects") or 0,
            "prior_total": prior.get("total_discovered_prospects") or 0,
        }
        row = self.icp.get_or_create()
        if hasattr(row, "discovery_coverage_json"):
            row.discovery_coverage_json = snapshot
        else:
            metrics = dict(row.learning_metrics_json or {})
            metrics["discovery_coverage"] = snapshot
            row.learning_metrics_json = metrics
        self.db.add(row)
        self.db.commit()
        return snapshot
