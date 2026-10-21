"""Acquisition orchestration boundary for Admin-driven discovery/activation.

Independently testable pipeline:
  discover → research → score → queue → personalize → activate

Continuous scheduling lives in AutonomousAutomationService.
Instantly only via AutonomousActivationService when dry_run=False.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.base import DiscoveryProvider, DiscoveryProviderError, ResearchProvider
from acquisition.integrations.discovery.types import DiscoveryCandidate
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.models.prospect import Prospect
from acquisition.services.autonomous_activation_service import AutonomousActivationService
from acquisition.services.autonomous_discovery_service import AutonomousProspectDiscoveryService
from acquisition.services.discovery_cycle_semantics import (
    effective_scan_cap,
    effective_usable_limit,
    is_usable_discovery_result,
    outcome_acquisition_status,
    progress_reason_for_status,
    should_skip_confidently_excluded,
    summarize_stop_reason,
)
from acquisition.services.personalized_outreach_service import PersonalizedOutreachService
from acquisition.services.prospect_identity import (
    domain_from_website_or_email,
    find_existing_company_or_prospect,
    normalize_domain,
)
from acquisition.services.public_research_service import PublicResearchService
from acquisition.services import acquisition_trace as acq_trace
from acquisition.services import live_trace

logger = logging.getLogger(__name__)


class AcquisitionOrchestrationService:
    """Thin orchestrator Admin / automation can invoke."""

    def __init__(
        self,
        db: Session,
        *,
        discovery_provider: DiscoveryProvider | None = None,
        research_provider: ResearchProvider | None = None,
        settings: Settings | None = None,
        instantly_client: InstantlyClient | None = None,
        outreach: PersonalizedOutreachService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.discovery = AutonomousProspectDiscoveryService(
            db, provider=discovery_provider, settings=self.settings
        )
        self.research = PublicResearchService(
            db, provider=research_provider, settings=self.settings
        )
        self.activation = AutonomousActivationService(
            db,
            settings=self.settings,
            instantly_client=instantly_client,
            outreach=outreach,
        )

    def discover_candidates(self, *, limit: int | None = None) -> list[Any]:
        return self.discovery.discover_candidates(limit=limit)

    def research_prospect(self, prospect_id: uuid.UUID) -> dict[str, Any]:
        return self.research.research_prospect(prospect_id)

    def research_and_score_prospect(self, prospect_id: uuid.UUID) -> dict[str, Any]:
        return self.research.research_and_score_prospect(prospect_id)

    def build_activation_queue(self, *, limit: int | None = None) -> dict[str, Any]:
        return self.activation.build_activation_queue(limit=limit)

    def preview_activation(self, prospect_id: uuid.UUID) -> dict[str, Any]:
        return self.activation.preview_activation(prospect_id)

    def activate_prospect(
        self, prospect_id: uuid.UUID, *, dry_run: bool = True
    ) -> dict[str, Any]:
        return self.activation.activate_prospect(prospect_id, dry_run=dry_run)

    def run_activation_batch(
        self, *, limit: int | None = None, dry_run: bool = True
    ) -> dict[str, Any]:
        return self.activation.run_activation_batch(limit=limit, dry_run=dry_run)

    def run_discovery_cycle(
        self,
        *,
        limit: int | None = None,
        research: bool = True,
        score: bool = True,
    ) -> dict[str, Any]:
        """Run a bounded discovery cycle producing up to `limit` USABLE prospects.

        `limit` (Admin Prospects per run) counts final usable researched/scored
        prospects only — excluded / unreachable / failed / needs-more-research do
        not consume the quota. Candidate inspection is bounded by
        discovery_scan_hard_max. Never calls Instantly/outreach.
        """
        if not research:
            # Legacy discover-only path: upsert provisional candidates by limit.
            discovery = self.discovery.run_discovery_cycle(limit=limit)
            return {
                "discovery": discovery,
                "research": [],
                "usable_count": 0,
                "candidates_inspected": int(discovery.get("candidates") or 0),
                "stop_reason": "research_disabled",
            }

        icp_limit = int(self.discovery.icp.as_dict().get("discovery_cycle_limit") or 0)
        usable_limit = effective_usable_limit(self.settings, limit, icp_limit)
        scan_cap = effective_scan_cap(self.settings, usable_limit=usable_limit)
        status = self.discovery.provider_status()
        if not status.get("configured"):
            discovery = {
                "ok": False,
                "provider": status,
                "limit": usable_limit,
                "scan_cap": scan_cap,
                "candidates": 0,
                "created": 0,
                "updated": 0,
                "usable_count": 0,
                "candidates_inspected": 0,
                "skipped_excluded": 0,
                "errors": ["discovery_provider_configuration_required"],
                "results": [],
                "usable_results": [],
                "stop_reason": "provider_not_configured",
            }
            return {
                "discovery": discovery,
                "research": [],
                "usable_count": 0,
                "candidates_inspected": 0,
                "stop_reason": "provider_not_configured",
            }

        try:
            if getattr(self.discovery.provider, "name", "") == "openai_agentic_discovery":
                if hasattr(self.discovery.provider, "run_cycle"):
                    agentic_out = self.discovery.provider.run_cycle(limit=usable_limit, preview=False)
                else:
                    from acquisition.services.agentic_discovery_service import AgenticDiscoveryService

                    agentic_out = AgenticDiscoveryService(
                        self.db, settings=self.settings
                    ).run_cycle(limit=usable_limit, preview=False)
                discovery = {
                    "ok": bool(agentic_out.get("ok")),
                    "provider": agentic_out.get("provider") or status,
                    "limit": usable_limit,
                    "scan_cap": usable_limit,
                    "candidates": int(agentic_out.get("candidates") or 0),
                    "created": int(agentic_out.get("created") or 0),
                    "updated": int(agentic_out.get("updated") or 0),
                    "duplicates": int(agentic_out.get("duplicates") or 0),
                    "excluded": int(agentic_out.get("excluded") or 0),
                    "usable_count": int(agentic_out.get("usable_count") or 0),
                    "ready_count": int(agentic_out.get("ready_count") or 0),
                    "candidates_inspected": int(
                        agentic_out.get("candidates_inspected") or agentic_out.get("candidates") or 0
                    ),
                    "skipped_excluded": int(agentic_out.get("excluded") or 0),
                    "errors": list(agentic_out.get("errors") or []),
                    "results": list(agentic_out.get("results") or []),
                    "usable_results": list(agentic_out.get("usable_results") or []),
                    "stop_reason": agentic_out.get("stop_reason"),
                    "agentic": True,
                    "cost_usd": agentic_out.get("cost_usd"),
                    "budget": agentic_out.get("budget"),
                    "coverage": agentic_out.get("coverage"),
                    "requested_count": agentic_out.get("requested_count") or usable_limit,
                    "found_count": agentic_out.get("found_count"),
                    "shortfall_count": agentic_out.get("shortfall_count"),
                    "shortfall_reason": agentic_out.get("shortfall_reason"),
                    "search_attempts": agentic_out.get("search_attempts"),
                    "discovery_attempts": agentic_out.get("discovery_attempts"),
                    "enrichment": agentic_out.get("enrichment"),
                    "slots": agentic_out.get("slots"),
                    "ai_usage": agentic_out.get("ai_usage"),
                }
                return {
                    "discovery": discovery,
                    "research": [],
                    # Agentic success metric is FINAL outreach-ready, not merely usable.
                    "usable_count": discovery["usable_count"],
                    "ready_count": discovery["ready_count"],
                    "candidates_inspected": discovery["candidates_inspected"],
                    "stop_reason": discovery.get("stop_reason"),
                    "agentic": True,
                }

            fetch_limit = scan_cap
            if getattr(self.discovery.provider, "name", "") == "multi_source_discovery":
                # Multi-source harvests per-source fetch limits, hard-filters,
                # then AI-triages the FULL surviving pool and returns only the
                # top requested count for research.
                fetch_limit = usable_limit
                scan_cap = fetch_limit
            candidates = self.discovery.discover_candidates(limit=fetch_limit)
        except DiscoveryProviderError as exc:
            discovery = {
                "ok": False,
                "provider": status,
                "limit": usable_limit,
                "scan_cap": scan_cap,
                "candidates": 0,
                "created": 0,
                "updated": 0,
                "usable_count": 0,
                "candidates_inspected": 0,
                "skipped_excluded": 0,
                "errors": [str(exc)],
                "results": [],
                "usable_results": [],
                "stop_reason": "discovery_provider_error",
            }
            return {
                "discovery": discovery,
                "research": [],
                "usable_count": 0,
                "candidates_inspected": 0,
                "stop_reason": "discovery_provider_error",
            }

        created = 0
        updated = 0
        usable_new = 0
        research_cost = 0.0
        slice_new: dict[str, int] = {}
        skipped_excluded = 0
        candidates_inspected = 0
        errors: list[str] = []
        results: list[dict[str, Any]] = []
        usable_results: list[dict[str, Any]] = []
        researched: list[dict[str, Any]] = []

        for candidate in candidates:
            if len(usable_results) >= usable_limit:
                break
            if candidates_inspected >= scan_cap:
                break

            candidates_inspected += 1
            existing = self._lookup_existing(candidate)
            if existing is not None and should_skip_confidently_excluded(
                existing, settings=self.settings
            ):
                skipped_excluded += 1
                acq_trace.trace_skip_known_excluded(
                    company=existing.company or candidate.company,
                    domain=existing.domain,
                    prospect_id=existing.id,
                    status=(
                        existing.acquisition_status.value
                        if existing.acquisition_status
                        else None
                    ),
                    reason="Already confidently excluded — skipping re-research",
                )
                acq_trace.trace_progress(
                    requested_usable=usable_limit,
                    candidates_inspected=candidates_inspected,
                    usable_found=len(usable_results),
                    decision="CONTINUE",
                    reason="Already-known excluded duplicate skipped",
                )
                continue

            try:
                persist = self.discovery.upsert_candidate(candidate)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()  # keep the session usable for the next candidate
                live_trace.event(
                    "error",
                    f"Could not save {candidate.company}: {str(exc).splitlines()[0][:200]}",
                    level="error",
                    company=candidate.company,
                )
                logger.exception("failed to upsert discovery candidate %s", candidate.company)
                errors.append(f"{candidate.company}: {exc}")
                acq_trace.trace_progress(
                    requested_usable=usable_limit,
                    candidates_inspected=candidates_inspected,
                    usable_found=len(usable_results),
                    decision="CONTINUE",
                    reason="Candidate upsert failed",
                )
                continue

            if persist.created:
                created += 1
            else:
                updated += 1
            if candidate.slice_key:
                slice_new[candidate.slice_key] = slice_new.get(candidate.slice_key, 0) + (
                    1 if persist.created else 0
                )
            row = {
                "prospect_id": str(persist.prospect_id),
                "created": persist.created,
                "company": persist.company,
                "domain": persist.domain,
                "source": candidate.discovery_source,
            }
            results.append(row)
            logger.info(
                "researching %s (%s)",
                persist.company,
                "new" if persist.created else "refresh",
            )

            try:
                if score:
                    outcome = self.research_and_score_prospect(persist.prospect_id)
                else:
                    outcome = self.research_prospect(persist.prospect_id)
                researched.append(outcome)
                research_cost += float(outcome.get("cost_usd") or 0.0)
                if acq_trace.is_acquisition_trace_enabled(self.settings) or live_trace.is_active():
                    self._trace_done_from_outcome(persist.prospect_id, outcome)
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                logger.exception("orchestration research failed for %s", persist.prospect_id)
                outcome = {
                    "ok": False,
                    "prospect_id": str(persist.prospect_id),
                    "errors": [str(exc)],
                    "corrupted": False,
                    "acquisition_status": None,
                }
                researched.append(outcome)

            final_status = outcome_acquisition_status(outcome)
            if not final_status:
                prospect = self.db.get(Prospect, persist.prospect_id)
                final_status = (
                    prospect.acquisition_status.value
                    if prospect and prospect.acquisition_status
                    else None
                )
            row["acquisition_status"] = final_status
            if outcome.get("merged") and persist.created:
                # The "new" company turned out to be an existing one — don't count it.
                created -= 1
                updated += 1
                if candidate.slice_key:
                    slice_new[candidate.slice_key] = max(0, slice_new.get(candidate.slice_key, 1) - 1)
            usable = bool(outcome.get("ok")) and is_usable_discovery_result(final_status) and not outcome.get("merged")
            row["usable"] = usable
            if usable and persist.created:
                usable_new += 1
            if not outcome.get("ok"):
                live_trace.event(
                    "error",
                    f"Research failed for {persist.company}: {'; '.join(outcome.get('errors') or [])[:200]}",
                    level="error",
                    company=persist.company,
                    prospect_id=persist.prospect_id,
                )
            if usable:
                usable_results.append(row)
                decision = (
                    "STOP" if len(usable_results) >= usable_limit else "CONTINUE"
                )
                reason = (
                    "Requested usable prospect count reached"
                    if decision == "STOP"
                    else progress_reason_for_status(final_status)
                )
                acq_trace.trace_progress(
                    requested_usable=usable_limit,
                    candidates_inspected=candidates_inspected,
                    usable_found=len(usable_results),
                    decision=decision,
                    reason=reason,
                )
            else:
                acq_trace.trace_progress(
                    requested_usable=usable_limit,
                    candidates_inspected=candidates_inspected,
                    usable_found=len(usable_results),
                    decision="CONTINUE",
                    reason=(
                        "Research failed"
                        if not outcome.get("ok")
                        else progress_reason_for_status(final_status)
                    ),
                )

        stop_reason = summarize_stop_reason(
            usable_found=len(usable_results),
            usable_limit=usable_limit,
            candidates_inspected=candidates_inspected,
            scan_cap=scan_cap,
            candidate_pool=len(candidates),
        )
        if len(usable_results) >= usable_limit:
            # Ensure final STOP line if last progress was CONTINUE somehow.
            pass
        elif candidates_inspected > 0 and len(usable_results) < usable_limit:
            acq_trace.trace_progress(
                requested_usable=usable_limit,
                candidates_inspected=candidates_inspected,
                usable_found=len(usable_results),
                decision="STOP",
                reason=stop_reason,
            )
        elif candidates_inspected == 0:
            acq_trace.trace_progress(
                requested_usable=usable_limit,
                candidates_inspected=0,
                usable_found=0,
                decision="STOP",
                reason=stop_reason,
            )

        # Feed yield back into the slice ledger so empty slices rest.
        if slice_new:
            from acquisition.services.discovery_slice_service import DiscoverySliceService

            ledger = DiscoverySliceService(self.db)
            for key, n in slice_new.items():
                ledger.record_yield(key, n)
        pstats = getattr(self.discovery.provider, "last_stats", None)
        harvest_cost = float(getattr(pstats, "harvest_cost_usd", 0.0) or 0.0)
        known_skipped = int(getattr(pstats, "known_skipped", 0) or 0)
        rejected = int(getattr(pstats, "rejected", 0) or 0) + int(getattr(pstats, "verify_failed", 0) or 0)
        by_source = dict(getattr(pstats, "by_source", {}) or {})
        self._emit_post_selection_trace_summaries(researched)
        live_trace.event(
            "run",
            f"Run finished: {created} new companies, {updated} refreshed, {usable_new} new usable · "
            f"{known_skipped} already-known skipped · {rejected} rejected · "
            f"est. cost ${harvest_cost + research_cost:.3f}",
            level="success" if created else "warning",
            new=created,
            refreshed=updated,
            usable=len(usable_results),
            usable_new=usable_new,
            known_skipped=known_skipped,
            rejected=rejected,
            harvest_cost_usd=round(harvest_cost, 4),
            research_cost_usd=round(research_cost, 4),
            stop_reason=stop_reason,
            by_source=by_source,
        )

        discovery = {
            "ok": True,
            "provider": status,
            "limit": usable_limit,
            "scan_cap": scan_cap,
            "new_count": created,
            "refreshed_count": updated,
            "usable_new_count": usable_new,
            "known_skipped": known_skipped,
            "rejected": rejected,
            "cost_usd": round(harvest_cost + research_cost, 4),
            "harvest_cost_usd": round(harvest_cost, 4),
            "research_cost_usd": round(research_cost, 4),
            "by_source": by_source,
            "candidates": len(candidates),
            "candidates_inspected": candidates_inspected,
            "created": created,
            "updated": updated,
            "usable_count": len(usable_results),
            "skipped_excluded": skipped_excluded,
            "errors": errors,
            "results": results,
            "usable_results": usable_results,
            "stop_reason": stop_reason,
            "icp_geography": self.discovery.icp.as_dict().get("geography"),
        }
        return {
            "discovery": discovery,
            "research": researched,
            "usable_count": len(usable_results),
            "candidates_inspected": candidates_inspected,
            "stop_reason": stop_reason,
        }

    def run_full_cycle(
        self,
        *,
        discovery_limit: int | None = None,
        activation_limit: int | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """discover → research/score → queue → optional activate (dry-run by default)."""
        discovery = self.run_discovery_cycle(
            limit=discovery_limit, research=True, score=True
        )
        activation = self.run_activation_batch(limit=activation_limit, dry_run=dry_run)
        return {
            "discovery": discovery,
            "activation": activation,
        }

    def run_live_e2e_preview(
        self,
        *,
        discovery_limit: int | None = None,
        activate_one: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Bounded Admin/dev live path: discover → research/score → queue → outreach preview.

        Never mass-sends. Activation of at most one prospect requires activate_one=True
        and is dry-run by default unless explicitly overridden with dry_run=False.
        """
        hard = int(self.settings.discovery_cycle_hard_max or 25)
        e2e_default = int(self.settings.live_e2e_discovery_limit or 2)
        limit = max(1, min(int(discovery_limit or e2e_default), min(5, hard)))
        discovery = self.run_discovery_cycle(limit=limit, research=True, score=True)
        queue = self.build_activation_queue(limit=min(5, hard))
        previews: list[dict[str, Any]] = []
        for row in (queue.get("selected") or [])[:3]:
            try:
                previews.append(self.preview_activation(uuid.UUID(str(row["prospect_id"]))))
            except Exception as exc:  # noqa: BLE001
                previews.append(
                    {
                        "ok": False,
                        "prospect_id": row.get("prospect_id"),
                        "errors": [str(exc)],
                    }
                )
        activation: dict[str, Any] | None = None
        if activate_one and (queue.get("selected") or []):
            # Safety: live activation also gated by settings flag.
            allow_live = bool(self.settings.live_e2e_activation_enabled)
            effective_dry = dry_run if allow_live else True
            pid = uuid.UUID(str(queue["selected"][0]["prospect_id"]))
            activation = self.activate_prospect(pid, dry_run=effective_dry)
            activation["forced_dry_run"] = not allow_live and not dry_run
        return {
            "ok": True,
            "limit": limit,
            "discovery": discovery,
            "queue": {
                "selected_count": queue.get("selected_count"),
                "skipped_count": queue.get("skipped_count"),
                "selected": queue.get("selected"),
            },
            "outreach_previews": previews,
            "activation": activation,
            "mass_send": False,
        }

    def _lookup_existing(self, candidate: DiscoveryCandidate) -> Prospect | None:
        domain = normalize_domain(candidate.domain) or domain_from_website_or_email(
            website=candidate.website, email=None
        )
        return find_existing_company_or_prospect(
            self.db,
            website=candidate.website,
            domain=domain,
            company=candidate.company,
            executive_name=None,
        )


    def _emit_post_selection_trace_summaries(self, researched: list[dict[str, Any]]) -> None:
        """Emit one research / scoring / decision summary for Live Trace (not per company)."""
        if not researched:
            return
        companies: list[dict[str, Any]] = []
        dm_found = 0
        email_found = 0
        research_ok = 0
        research_fail = 0
        status_counts: dict[str, int] = {}
        score_rows: list[dict[str, Any]] = []

        for outcome in researched:
            pid = outcome.get("prospect_id")
            prospect = None
            try:
                if pid:
                    prospect = self.db.get(Prospect, uuid.UUID(str(pid)))
            except (ValueError, TypeError):
                prospect = None
            company = (prospect.company if prospect else None) or "unknown"
            ok = bool(outcome.get("ok"))
            if ok:
                research_ok += 1
            else:
                research_fail += 1
            dm = getattr(prospect, "executive_name", None) if prospect else None
            email = getattr(prospect, "email", None) if prospect else None
            if dm:
                dm_found += 1
            if email:
                email_found += 1
            scoring = (outcome or {}).get("scoring") or {}
            fit = (scoring.get("fit") or {}).get("score")
            timing = (scoring.get("timing") or {}).get("score")
            reach = (scoring.get("reachability") or {}).get("score")
            priority = (
                (scoring.get("effective_priority") or {}).get("score")
                or (scoring.get("priority") or {}).get("score")
            )
            status = outcome_acquisition_status(outcome) or (
                prospect.acquisition_status.value
                if prospect and prospect.acquisition_status
                else "unknown"
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            row = {
                "company": company,
                "prospect_id": str(pid) if pid else None,
                "ok": ok,
                "decision_maker": dm,
                "email": email,
                "fit": fit,
                "timing": timing,
                "reachability": reach,
                "priority": priority,
                "status": status,
                "errors": outcome.get("errors") or [],
            }
            companies.append(row)
            if any(v is not None for v in (fit, timing, reach, priority)):
                score_rows.append(row)

        live_trace.event(
            "research",
            (
                f"Research summary: selected={len(researched)}, completed={research_ok}, "
                f"decision_makers={dm_found}, business_emails={email_found}, failures={research_fail}"
            ),
            level="success" if research_fail == 0 else "warning",
            selected_for_research=len(researched),
            research_completed=research_ok,
            decision_makers_found=dm_found,
            business_emails_found=email_found,
            research_failures=research_fail,
            companies=companies,
        )
        if score_rows:
            live_trace.event(
                "score",
                f"Scoring summary: {len(score_rows)} compan{'y' if len(score_rows) == 1 else 'ies'}",
                level="success",
                companies=[
                    {
                        "company": r["company"],
                        "fit": r["fit"],
                        "timing": r["timing"],
                        "reachability": r["reachability"],
                        "priority": r["priority"],
                        "status": r["status"],
                    }
                    for r in score_rows
                ],
            )
        live_trace.event(
            "decision",
            (
                "Decision summary: "
                + ", ".join(f"{k.replace('_', ' ')}={v}" for k, v in sorted(status_counts.items()))
            ),
            level="success",
            status_counts=status_counts,
            companies=[{"company": c["company"], "status": c["status"], "priority": c["priority"]} for c in companies],
        )

    def _trace_done_from_outcome(self, prospect_id: uuid.UUID, outcome: dict[str, Any]) -> None:
        prospect = self.db.get(Prospect, prospect_id)
        scoring = (outcome or {}).get("scoring") or {}
        learning = scoring.get("learning") or {}
        location = None
        if prospect is not None:
            location = ", ".join(
                p for p in [prospect.city, prospect.state] if p
            ) or None
        acq_trace.trace_done(
            prospect_id=prospect_id,
            company=(prospect.company if prospect else None) or "unknown",
            domain=prospect.domain if prospect else None,
            location=location,
            discovery_source=(prospect.discovery_source or prospect.source) if prospect else None,
            fit=(scoring.get("fit") or {}).get("score")
            if scoring
            else (prospect.fit_score if prospect else None),
            timing=(scoring.get("timing") or {}).get("score")
            if scoring
            else (prospect.timing_score if prospect else None),
            reachability=(scoring.get("reachability") or {}).get("score")
            if scoring
            else (prospect.reachability_score if prospect else None),
            base_priority=(scoring.get("priority") or {}).get("score")
            if scoring
            else (prospect.priority_score if prospect else None),
            ai_adjustment=learning.get("learning_adjustment")
            if learning
            else (prospect.learning_adjustment if prospect else None),
            effective_priority=(
                (scoring.get("effective_priority") or {}).get("score")
                if scoring
                else None
            )
            or learning.get("effective_priority_score")
            or (prospect.effective_priority_score if prospect else None),
            status=(
                outcome.get("acquisition_status")
                or (prospect.acquisition_status.value if prospect and prospect.acquisition_status else None)
            ),
        )
