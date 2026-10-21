"""Continuous autonomous cycles — schedules call the shared orchestration services.

Uses PostgreSQL advisory locks so overlapping processes cannot double-activate.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings, is_non_production_environment
from acquisition.integrations.discovery.factory import (
    is_live_discovery_provider_name,
    is_mock_discovery_provider_name,
)
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.models.prospect import AutonomousRunHistory
from acquisition.services.acquisition_capacity_service import AcquisitionCapacityService
from acquisition.services.acquisition_orchestration_service import AcquisitionOrchestrationService
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.personalized_outreach_service import PersonalizedOutreachService
from acquisition.services import live_trace
from acquisition.time_format import format_datetime_12h

logger = logging.getLogger(__name__)

# Stable int keys for pg_advisory_lock (discovery / activation).
_LOCK_DISCOVERY = 4_201_001
_LOCK_ACTIVATION = 4_201_002


class AutonomousAutomationService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        orchestration: AcquisitionOrchestrationService | None = None,
        instantly_client: InstantlyClient | None = None,
        outreach: PersonalizedOutreachService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.icp = IcpSettingsService(db)
        self.capacity = AcquisitionCapacityService(db, settings=self.settings)
        self.orch = orchestration or AcquisitionOrchestrationService(
            db,
            settings=self.settings,
            instantly_client=instantly_client,
            outreach=outreach,
        )

    # ----- public entry points -----

    def operational_status(self) -> dict[str, Any]:
        cfg = self.icp.as_dict()
        cap = self.capacity.as_dict()
        provider = self.orch.discovery.provider_status()
        research = self.orch.research.provider.status()
        last_discovery = self._last_run("discovery")
        last_activation = self._last_run("activation")
        provider_name = (self.settings.discovery_provider or "none").strip().lower()
        live = is_live_discovery_provider_name(provider_name)
        mock = is_mock_discovery_provider_name(provider_name)
        return {
            "engine_enabled": bool(
                cfg["discovery_enabled"] or cfg["autonomous_outreach_enabled"]
            ),
            "discovery_enabled": cfg["discovery_enabled"],
            "autonomous_outreach_enabled": cfg["autonomous_outreach_enabled"],
            "discovery_cycle_limit": cfg["discovery_cycle_limit"],
            "discovery_interval_minutes": cfg["discovery_interval_minutes"],
            "activation_batch_limit": cfg["activation_batch_limit"],
            "activation_interval_minutes": cfg["activation_interval_minutes"],
            "capacity": cap,
            "provider": provider,
            "research_provider": {
                "configured": research.configured,
                "provider_name": research.provider_name,
                "message": research.message,
            },
            "provider_mode": (
                "live" if live else ("mock" if mock else ("none" if provider_name in {"", "none"} else provider_name))
            ),
            "provider_safe_for_production": self._provider_allowed(cfg),
            "last_discovery": last_discovery,
            "last_activation": last_activation,
            "last_successful_live_discovery": self._last_successful_live_discovery(),
            "scheduler": {
                "inprocess_enabled": bool(self.settings.autonomous_scheduler_inprocess),
                "tick_seconds": int(self.settings.autonomous_scheduler_tick_seconds or 60),
                "approach": "cli_loop_or_optional_inprocess",
            },
        }

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        """Scheduler tick: run due discovery and/or activation cycles."""
        cfg = self.icp.as_dict()
        if not cfg["discovery_enabled"] and not cfg["autonomous_outreach_enabled"]:
            return {"ok": True, "skipped": True, "reason": "automation_disabled"}
        out: dict[str, Any] = {"ok": True, "discovery": None, "activation": None}
        if cfg["discovery_enabled"] and (force or self._is_due("discovery", cfg)):
            out["discovery"] = self.run_discovery_cycle(manual=False)
        elif cfg["discovery_enabled"]:
            out["discovery"] = {"skipped": True, "reason": "not_due"}
        else:
            out["discovery"] = {"skipped": True, "reason": "discovery_disabled"}

        if cfg["autonomous_outreach_enabled"] and (force or self._is_due("activation", cfg)):
            out["activation"] = self.run_activation_cycle(dry_run=False, manual=False)
        elif cfg["autonomous_outreach_enabled"]:
            out["activation"] = {"skipped": True, "reason": "not_due"}
        else:
            out["activation"] = {"skipped": True, "reason": "outreach_disabled"}
        return out

    def start_discovery_run_row(self) -> AutonomousRunHistory:
        """Create the run row up-front (Admin 'Run now' opens Live Trace immediately)."""
        return self._start_run("discovery", dry_run=False)

    def start_discovery_preview_run_row(self) -> AutonomousRunHistory:
        """Create a preview run row (harvest + triage only; no prospect writes)."""
        return self._start_run("discovery_preview", dry_run=True)

    def run_discovery_preview_cycle(
        self, *, run: AutonomousRunHistory | None = None
    ) -> dict[str, Any]:
        """Harvest + cheap filter + AI triage only. No upsert/research/outreach."""
        run = run or self.start_discovery_preview_run_row()
        with live_trace.run(run.id, self.db):
            live_trace.event(
                "run",
                "Discovery Preview started — stops after candidate selection",
                provider=(self.settings.discovery_provider or "none"),
                preview=True,
            )
            try:
                result = self._run_discovery_preview_inner(run)
            except Exception as exc:  # noqa: BLE001
                logger.exception("discovery preview failed")
                live_trace.event("error", f"Preview crashed: {exc}", level="error")
                return self._finish_failed(run, str(exc))
            if not result.get("ok"):
                live_trace.event(
                    "run",
                    f"Preview ended: {result.get('error') or result.get('reason') or 'failed'}",
                    level="error",
                )
            else:
                live_trace.event(
                    "run",
                    "Discovery Preview finished — no prospects saved or researched",
                    level="success",
                    selected_count=result.get("selected_count"),
                )
            return result

    def _run_discovery_preview_inner(self, run: AutonomousRunHistory) -> dict[str, Any]:
        cfg = self.icp.as_dict()
        allowed, reason = self._provider_gate(cfg)
        if not allowed:
            return self._finish_failed(run, reason)
        if not self._try_lock(_LOCK_DISCOVERY):
            return self._finish_skipped(run, "lock_not_acquired")
        try:
            limit = int(cfg.get("discovery_cycle_limit") or 5)
            hard = int(self.settings.discovery_cycle_hard_max or 25)
            limit = max(1, min(limit, hard))
            provider_name = getattr(self.orch.discovery.provider, "name", "")
            if provider_name == "openai_agentic_discovery":
                agentic_out = self.orch.discovery.provider.run_cycle(limit=limit, preview=True)
                selected = [
                    {
                        "company": p.get("company_name"),
                        "domain": p.get("normalized_domain"),
                        "source": "openai_agentic",
                        "state": (p.get("location") or {}).get("state"),
                        "fit_score": p.get("fit_score"),
                        "timing_score": p.get("timing_score"),
                        "reachability_score": p.get("reachability_score"),
                    }
                    for p in (agentic_out.get("returned_prospects") or [])
                ]
                return self._finish_ok(
                    run,
                    discovered=0,
                    researched=0,
                    scored=0,
                    skipped=0,
                    failed=0,
                    summary={
                        "discovery": {
                            "preview": True,
                            "agentic": True,
                            "new_count": 0,
                            "candidates": int(agentic_out.get("candidates") or 0),
                            "selected": selected,
                            "stop_reason": "preview_complete",
                            "cost_usd": agentic_out.get("cost_usd"),
                        },
                        "selected_count": len(selected),
                    },
                )
            if provider_name != "multi_source_discovery":
                return self._finish_failed(
                    run,
                    "discovery_preview_requires_multi_provider",
                )
            candidates = self.orch.discovery.discover_candidates(limit=limit, preview=True)
            stats = getattr(self.orch.discovery.provider, "last_stats", None)
            triage = getattr(stats, "triage", None) or {}
            selected = [
                {
                    "company": c.company,
                    "domain": c.domain,
                    "source": c.discovery_source,
                    "slice_key": c.slice_key,
                    "state": c.state,
                    "triage_score": ((c.raw or {}).get("triage") or {}).get("score"),
                    "reason": ((c.raw or {}).get("triage") or {}).get("reason"),
                }
                for c in candidates
            ]
            out = self._finish_ok(
                run,
                discovered=0,
                researched=0,
                scored=0,
                skipped=int(getattr(stats, "known_skipped", 0) or 0),
                failed=0,
                summary={
                    "discovery": {
                        "preview": True,
                        "new_count": 0,
                        "refreshed_count": 0,
                        "usable_count": 0,
                        "candidates": len(candidates),
                        "pool_target": getattr(stats, "pool_target", None),
                        "pool_available": getattr(stats, "pool_available", None),
                        "known_skipped": getattr(stats, "known_skipped", None),
                        "rejected": getattr(stats, "rejected", None),
                        "verify_failed": getattr(stats, "verify_failed", None),
                        "cost_usd": round(float(getattr(stats, "harvest_cost_usd", 0) or 0), 4),
                        "by_source": getattr(stats, "by_source", None),
                        "triage": triage,
                        "selected": selected,
                        "stop_reason": "preview_complete",
                    },
                    "selected_count": len(selected),
                },
            )
            out["selected_count"] = len(selected)
            return out
        finally:
            self._unlock(_LOCK_DISCOVERY)

    def run_discovery_cycle(
        self, *, manual: bool = True, run: AutonomousRunHistory | None = None
    ) -> dict[str, Any]:
        run = run or self._start_run("discovery", dry_run=False)
        with live_trace.run(run.id, self.db):
            live_trace.event(
                "run",
                f"Discovery run started ({'manual' if manual else 'scheduled'})",
                provider=(self.settings.discovery_provider or "none"),
            )
            result = self._run_discovery_cycle_inner(run, manual=manual)
            if not result.get("ok"):
                live_trace.event(
                    "run",
                    f"Run ended: {result.get('error') or result.get('reason') or 'failed'}",
                    level="error",
                )
            elif result.get("skipped"):
                live_trace.event("run", f"Run skipped: {result.get('reason')}", level="warning")
            else:
                # Emit only after AutonomousRunHistory.status is committed completed
                # so Live Trace chrome and the timeline never disagree.
                live_trace.event(
                    "run",
                    "Run completed",
                    level="success",
                    usable=result.get("run", {}).get("discovered_count")
                    if isinstance(result.get("run"), dict)
                    else None,
                )
            return result

    def _run_discovery_cycle_inner(self, run: AutonomousRunHistory, *, manual: bool) -> dict[str, Any]:
        cfg = self.icp.as_dict()
        if not cfg["discovery_enabled"] and not manual:
            return self._finish_skipped(run, "discovery_disabled")

        allowed, reason = self._provider_gate(cfg)
        if not allowed:
            return self._finish_failed(run, reason)

        if not self._try_lock(_LOCK_DISCOVERY):
            return self._finish_skipped(run, "lock_not_acquired")

        try:
            from acquisition.services.acquisition_cost_estimator import AcquisitionCostEstimator
            from acquisition.services.discovery_context_builder import DiscoveryContextBuilder
            from acquisition.services.openai_service import OpenAIService

            limit = int(cfg.get("discovery_cycle_limit") or 5)
            hard = int(self.settings.discovery_cycle_hard_max or 25)
            limit = max(1, min(limit, hard))
            admin = self.icp.as_dict()
            coverage = DiscoveryContextBuilder(self.db).build(admin)
            key_ok = bool(
                OpenAIService(db=self.db, settings=self.settings).get_summary().configured
            ) or bool((self.settings.openai_api_key or "").strip())
            pre = AcquisitionCostEstimator(self.settings).preflight(
                admin,
                requested_count=limit,
                coverage=coverage,
                api_key_configured=key_ok,
            )
            if not pre.get("safe_to_run"):
                problems = "; ".join(pre.get("problems") or ["preflight_failed"])[:500]
                return self._finish_failed(run, f"discovery_preflight_blocked:{problems}")

            result = self.orch.run_discovery_cycle(limit=limit, research=True, score=True)
            discovery = result.get("discovery") or {}
            research = result.get("research") or []
            researched = sum(1 for r in research if r.get("ok"))
            scored = sum(1 for r in research if (r.get("scoring") or {}).get("priority"))
            failed = sum(1 for r in research if not r.get("ok"))
            usable = int(
                result.get("usable_count")
                if result.get("usable_count") is not None
                else discovery.get("usable_count")
                or 0
            )
            ready = int(
                result.get("ready_count")
                if result.get("ready_count") is not None
                else discovery.get("ready_count")
                or 0
            )
            # Agentic runs count FINAL outreach-ready; legacy paths keep usable.
            discovered_metric = ready if (discovery.get("agentic") or result.get("agentic")) else usable
            # One failed research already isolated inside orchestration.
            return self._finish_ok(
                run,
                discovered=discovered_metric,
                researched=researched,
                scored=scored,
                failed=failed + len(discovery.get("errors") or []),
                skipped=int(discovery.get("skipped_excluded") or 0),
                summary={
                    "discovery": {
                        "new_count": discovery.get("new_count", discovery.get("created")),
                        "refreshed_count": discovery.get("refreshed_count", discovery.get("updated")),
                        "usable_new_count": discovery.get("usable_new_count"),
                        "known_skipped": discovery.get("known_skipped"),
                        "rejected": discovery.get("rejected"),
                        "cost_usd": discovery.get("cost_usd"),
                        "budget": discovery.get("budget"),
                        "by_source": discovery.get("by_source"),
                        "created": discovery.get("created"),
                        "updated": discovery.get("updated"),
                        "duplicates": discovery.get("duplicates"),
                        "excluded": discovery.get("excluded") or discovery.get("skipped_excluded"),
                        "candidates": discovery.get("candidates"),
                        "candidates_inspected": discovery.get("candidates_inspected"),
                        "usable_count": usable,
                        "ready_count": ready,
                        "skipped_excluded": discovery.get("skipped_excluded"),
                        "stop_reason": discovery.get("stop_reason") or result.get("stop_reason"),
                        "scan_cap": discovery.get("scan_cap"),
                        "errors": discovery.get("errors"),
                        "agentic": bool(discovery.get("agentic") or result.get("agentic")),
                        "limit": discovery.get("limit"),
                        "requested": discovery.get("requested_count") or discovery.get("limit"),
                        "requested_count": discovery.get("requested_count") or discovery.get("limit"),
                        "found_count": discovery.get("found_count"),
                        "shortfall_count": discovery.get("shortfall_count"),
                        "shortfall_reason": discovery.get("shortfall_reason"),
                        "search_attempts": discovery.get("search_attempts"),
                        "discovery_attempts": discovery.get("discovery_attempts"),
                        "enrichment": discovery.get("enrichment"),
                        "preflight_base_usd": (pre.get("estimate") or {}).get("base_run_usd"),
                    },
                    "provider": discovery.get("provider"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("discovery cycle failed")
            return self._finish_failed(run, str(exc))
        finally:
            self._unlock(_LOCK_DISCOVERY)

    def run_activation_cycle(
        self, *, dry_run: bool = True, manual: bool = True
    ) -> dict[str, Any]:
        cfg = self.icp.as_dict()
        run = self._start_run("activation", dry_run=dry_run)
        if not cfg["autonomous_outreach_enabled"] and not manual:
            return self._finish_skipped(run, "outreach_disabled")

        cap = self.capacity.assess()
        # Discovery independence: capacity only affects activation limit.
        effective_limit = cap.effective_activation_limit
        if effective_limit <= 0 and not dry_run:
            # Still record a paused cycle with zero activations; discovery unaffected.
            return self._finish_ok(
                run,
                queued=0,
                activated=0,
                skipped=0,
                failed=0,
                capacity_state=cap.state.value,
                summary={
                    "capacity": self.capacity.as_dict(),
                    "note": "capacity_paused_or_zero_limit",
                },
            )

        if not self._try_lock(_LOCK_ACTIVATION):
            return self._finish_skipped(run, "lock_not_acquired", capacity_state=cap.state.value)

        try:
            hard = int(self.settings.activation_batch_hard_max or 25)
            if dry_run:
                # Preview the queue through the same outreach path; never call Instantly.
                # Capacity PAUSED does not block dry-run preview (activated stays 0).
                limit = max(1, min(int(cfg.get("activation_batch_limit") or 5), hard))
            else:
                limit = max(0, min(int(effective_limit), hard))
            batch = self.orch.run_activation_batch(limit=limit, dry_run=dry_run)
            outcomes = batch.get("outcomes") or []
            activated = sum(1 for o in outcomes if o.get("activated"))
            failed = sum(1 for o in outcomes if not o.get("ok"))
            return self._finish_ok(
                run,
                queued=int(batch.get("queue", {}).get("selected_count") or 0),
                activated=activated,
                skipped=int(batch.get("queue", {}).get("skipped_count") or 0),
                failed=failed,
                capacity_state=cap.state.value,
                summary={
                    "batch": _safe_batch_summary(batch),
                    "capacity": self.capacity.as_dict(),
                    "effective_limit": limit,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("activation cycle failed")
            return self._finish_failed(run, str(exc), capacity_state=cap.state.value)
        finally:
            self._unlock(_LOCK_ACTIVATION)

    # ----- internals -----

    def _provider_allowed(self, cfg: dict[str, Any]) -> bool:
        allowed, _ = self._provider_gate(cfg)
        return allowed

    def _provider_gate(self, cfg: dict[str, Any]) -> tuple[bool, str]:
        provider_name = (self.settings.discovery_provider or "none").strip().lower()
        status = self.orch.discovery.provider_status()
        if provider_name in {"", "none"}:
            return False, "discovery_provider_configuration_required"
        if is_mock_discovery_provider_name(provider_name):
            if is_non_production_environment(self.settings.environment):
                return True, "ok"
            if cfg.get("allow_mock_discovery_in_production"):
                return True, "ok"
            return False, "mock_discovery_blocked_in_production"
        if is_live_discovery_provider_name(provider_name):
            if not status.get("configured"):
                return False, "discovery_provider_configuration_required"
            return True, "ok"
        if not status.get("configured"):
            return False, "discovery_provider_configuration_required"
        return True, "ok"

    def _last_successful_live_discovery(self) -> dict[str, Any] | None:
        if not is_live_discovery_provider_name(self.settings.discovery_provider):
            return None
        row = self._last_run_row("discovery", statuses=("completed",))
        if row is None:
            return None
        return {
            "id": str(row.id),
            "completed_at": format_datetime_12h(row.completed_at) if row.completed_at else None,
            "discovered_count": row.discovered_count,
            "researched_count": row.researched_count,
            "scored_count": row.scored_count,
        }

    def _is_due(self, cycle_type: str, cfg: dict[str, Any]) -> bool:
        last = self._last_run_row(cycle_type, statuses=("completed", "failed"))
        if last is None or last.completed_at is None:
            return True
        minutes = int(
            cfg.get(
                "discovery_interval_minutes"
                if cycle_type == "discovery"
                else "activation_interval_minutes"
            )
            or 1440
        )
        due_at = last.completed_at + timedelta(minutes=max(1, minutes))
        now = datetime.now(timezone.utc)
        completed = last.completed_at
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
            due_at = completed + timedelta(minutes=max(1, minutes))
        return now >= due_at

    def _last_run(self, cycle_type: str) -> dict[str, Any] | None:
        row = self._last_run_row(cycle_type)
        if row is None:
            return None
        return {
            "id": str(row.id),
            "status": row.status,
            "started_at": format_datetime_12h(row.started_at) if row.started_at else None,
            "completed_at": format_datetime_12h(row.completed_at) if row.completed_at else None,
            "discovered_count": row.discovered_count,
            "researched_count": row.researched_count,
            "scored_count": row.scored_count,
            "queued_count": row.queued_count,
            "activated_count": row.activated_count,
            "skipped_count": row.skipped_count,
            "failed_count": row.failed_count,
            "dry_run": row.dry_run,
            "capacity_state": row.capacity_state,
            "error_summary": row.error_summary,
        }

    def _last_run_row(
        self, cycle_type: str, statuses: tuple[str, ...] | None = None
    ) -> AutonomousRunHistory | None:
        stmt = select(AutonomousRunHistory).where(
            AutonomousRunHistory.cycle_type == cycle_type
        )
        if statuses:
            stmt = stmt.where(AutonomousRunHistory.status.in_(statuses))
        return self.db.scalar(stmt.order_by(AutonomousRunHistory.started_at.desc()).limit(1))

    def _start_run(self, cycle_type: str, *, dry_run: bool) -> AutonomousRunHistory:
        row = AutonomousRunHistory(
            id=uuid.uuid4(),
            cycle_type=cycle_type,
            status="running",
            started_at=datetime.now(timezone.utc),
            dry_run=dry_run,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _finish_ok(
        self,
        run: AutonomousRunHistory,
        *,
        discovered: int = 0,
        researched: int = 0,
        scored: int = 0,
        queued: int = 0,
        activated: int = 0,
        skipped: int = 0,
        failed: int = 0,
        capacity_state: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.discovered_count = discovered
        run.researched_count = researched
        run.scored_count = scored
        run.queued_count = queued
        run.activated_count = activated
        run.skipped_count = skipped
        run.failed_count = failed
        run.capacity_state = capacity_state
        run.summary_json = summary
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return {"ok": True, "skipped": False, "run": self._last_run(run.cycle_type)}

    def _finish_skipped(
        self,
        run: AutonomousRunHistory,
        reason: str,
        *,
        capacity_state: str | None = None,
    ) -> dict[str, Any]:
        run.status = "skipped"
        run.completed_at = datetime.now(timezone.utc)
        run.error_summary = reason
        run.capacity_state = capacity_state
        self.db.add(run)
        self.db.commit()
        return {"ok": True, "skipped": True, "reason": reason, "run_id": str(run.id)}

    def _finish_failed(
        self,
        run: AutonomousRunHistory,
        error: str,
        *,
        capacity_state: str | None = None,
    ) -> dict[str, Any]:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        run.error_summary = (error or "failed")[:2000]
        run.capacity_state = capacity_state
        self.db.add(run)
        self.db.commit()
        return {"ok": False, "skipped": False, "error": run.error_summary, "run_id": str(run.id)}

    def _try_lock(self, key: int) -> bool:
        return bool(self.db.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}))

    def _unlock(self, key: int) -> None:
        """Release discovery/activation advisory locks on this DB connection.

        Session-level advisory locks survive commits and can stick on pooled
        connections if unlock is skipped (e.g. uvicorn reload mid-run). Always
        attempt specific unlock, then unlock_all as a safety net.
        """
        try:
            self.db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            self.db.execute(text("SELECT pg_advisory_unlock_all()"))
            self.db.commit()
        except Exception:  # noqa: BLE001
            logger.warning("advisory unlock failed for %s", key)
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass


def _safe_batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempted": batch.get("attempted"),
        "activated_count": batch.get("activated_count"),
        "dry_run": batch.get("dry_run"),
        "queue_selected": (batch.get("queue") or {}).get("selected_count"),
        "queue_skipped": (batch.get("queue") or {}).get("skipped_count"),
    }
