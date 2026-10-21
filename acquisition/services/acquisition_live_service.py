"""Read models for the Admin Overview dashboard and Live Trace pages."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.prospect import (
    AcquisitionStatus,
    AcquisitionTraceEvent,
    AutonomousRunHistory,
    Prospect,
)
from acquisition.services import live_trace
from acquisition.services.live_trace_story import assemble_live_trace_story
from acquisition.time_format import format_datetime_12h

SOURCE_LABELS = {
    "openai_web": "OpenAI web search",
    "nppes": "NPPES registry",
    "exa": "Exa company index",
    "legacy_search": "Legacy search",
    "mock": "Mock (dev)",
}
TRACE_RETENTION_DAYS = 30

# Keys too large for the Live Trace timeline / poll JSON (kept in DB for replay).
_UI_OMIT_LIST_KEYS = frozenset({"surviving_pool"})
_UI_CAP_LIST_KEYS = {
    "accepted_companies": 40,
    "returned_companies": 40,
    "produced_companies": 40,
    "not_selected": 25,
    "failed_websites": 20,
    "known_skipped_companies": 20,
    "queries": 20,
    "slices": 20,
    "requests": 10,
}


def _detail_for_ui(detail: Any) -> Any:
    """Shrink event detail for browser polling without mutating stored rows."""
    if not isinstance(detail, dict):
        return detail
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if key in _UI_OMIT_LIST_KEYS and isinstance(value, list):
            out[key] = {"omitted": True, "count": len(value)}
            continue
        if key in _UI_CAP_LIST_KEYS and isinstance(value, list) and len(value) > _UI_CAP_LIST_KEYS[key]:
            lim = _UI_CAP_LIST_KEYS[key]
            out[key] = list(value[:lim]) + [{"_truncated": len(value) - lim}]
            continue
        out[key] = value
    return out


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ago(dt: datetime | None, now: datetime | None = None) -> str:
    dt = _as_utc(dt)
    if dt is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def run_row_dict(row: AutonomousRunHistory) -> dict[str, Any]:
    summary = row.summary_json or {}
    disc = summary.get("discovery") or {}
    started = _as_utc(row.started_at)
    completed = _as_utc(row.completed_at)
    duration = None
    if started and completed:
        duration = max(0, int((completed - started).total_seconds()))
    stop_reason = disc.get("stop_reason")
    saved = int(disc.get("new_count") or disc.get("created") or 0) + int(
        disc.get("refreshed_count") or disc.get("updated") or 0
    )
    usable = disc.get("usable_count", row.discovered_count)
    agentic = bool(disc.get("agentic"))
    if not agentic:
        prov = summary.get("provider")
        if isinstance(prov, dict):
            pname = str(prov.get("provider_name") or prov.get("name") or "").lower()
            pmsg = str(prov.get("message") or "").lower()
            if "agentic" in pname or "agentic" in pmsg:
                agentic = True
        elif "agentic" in str(prov or "").lower():
            agentic = True
    # Successful agentic saves must not surface legacy multi-source labels.
    if agentic and (saved > 0 or (usable or 0) > 0) and stop_reason in {
        "pool_exhausted",
        "quota_met",
        None,
    }:
        stop_reason = "completed"
    return {
        "id": str(row.id),
        "cycle_type": row.cycle_type,
        "status": row.status,
        "started_at": format_datetime_12h(row.started_at) if row.started_at else None,
        "started_ago": _ago(row.started_at),
        "duration_s": duration,
        "new": disc.get("new_count", disc.get("created")),
        "refreshed": disc.get("refreshed_count", disc.get("updated")),
        "usable": usable,
        "known_skipped": disc.get("known_skipped"),
        "rejected": disc.get("rejected"),
        "cost_usd": disc.get("cost_usd"),
        "by_source": disc.get("by_source") or {},
        "researched": row.researched_count,
        "scored": row.scored_count,
        "activated": row.activated_count,
        "queued": row.queued_count,
        "failed": row.failed_count,
        "error": row.error_summary,
        "stop_reason": stop_reason,
        "agentic": agentic,
        "requested": disc.get("requested") or disc.get("limit"),
        "duplicates": disc.get("duplicates"),
        "excluded": disc.get("excluded") or disc.get("skipped_excluded"),
    }


class AcquisitionLiveService:
    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    # ----- runs + events -----

    def recent_runs(self, *, limit: int = 30, cycle_type: str | None = None) -> list[dict[str, Any]]:
        stmt = select(AutonomousRunHistory)
        if cycle_type:
            stmt = stmt.where(AutonomousRunHistory.cycle_type == cycle_type)
        rows = self.db.scalars(stmt.order_by(AutonomousRunHistory.started_at.desc()).limit(limit)).all()
        counts = dict(
            self.db.execute(
                select(AcquisitionTraceEvent.run_id, func.count())
                .where(AcquisitionTraceEvent.run_id.in_([r.id for r in rows] or [uuid.uuid4()]))
                .group_by(AcquisitionTraceEvent.run_id)
            ).all()
        )
        out = []
        for r in rows:
            d = run_row_dict(r)
            d["event_count"] = int(counts.get(r.id, 0))
            out.append(d)
        return out

    def run(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.db.get(AutonomousRunHistory, run_id)
        return run_row_dict(row) if row else None

    def is_running(self) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        return bool(
            self.db.scalar(
                select(func.count())
                .select_from(AutonomousRunHistory)
                .where(AutonomousRunHistory.status == "running", AutonomousRunHistory.started_at >= cutoff)
            )
        )

    def events(
        self, run_id: uuid.UUID, *, after: int = 0, limit: int = 500, for_ui: bool = False
    ) -> list[dict[str, Any]]:
        rows = self.db.scalars(
            select(AcquisitionTraceEvent)
            .where(AcquisitionTraceEvent.run_id == run_id, AcquisitionTraceEvent.seq > after)
            .order_by(AcquisitionTraceEvent.seq.asc())
            .limit(limit)
        ).all()
        first = self.db.scalar(
            select(func.min(AcquisitionTraceEvent.created_at)).where(AcquisitionTraceEvent.run_id == run_id)
        )
        first = _as_utc(first)
        out = []
        for e in rows:
            created = _as_utc(e.created_at)
            offset = (created - first).total_seconds() if (created and first) else 0.0
            detail = e.detail_json
            if for_ui:
                detail = _detail_for_ui(detail)
            out.append(
                {
                    "seq": e.seq,
                    "stage": e.stage,
                    "level": e.level,
                    "title": e.title,
                    "company": e.company,
                    "prospect_id": str(e.prospect_id) if e.prospect_id else None,
                    "detail": detail,
                    "t": round(offset, 1),
                    "clock": created.strftime("%H:%M:%S") if created else "",
                }
            )
        return out

    def stage_counts(self, run_id: uuid.UUID) -> dict[str, int]:
        return {
            k: int(v)
            for k, v in self.db.execute(
                select(AcquisitionTraceEvent.stage, func.count())
                .where(AcquisitionTraceEvent.run_id == run_id)
                .group_by(AcquisitionTraceEvent.stage)
            ).all()
        }

    def story(self, run_id: uuid.UUID) -> dict[str, Any]:
        """Operator-facing grouped view built from existing trace events."""
        run = self.run(run_id) or {}
        # Full details (incl. surviving_pool) — needed to build source candidate tables.
        events = self.events(run_id, after=0, limit=5000, for_ui=False)
        return assemble_live_trace_story(events, run)

    def prune_old_events(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=TRACE_RETENTION_DAYS)
        res = self.db.execute(
            AcquisitionTraceEvent.__table__.delete().where(AcquisitionTraceEvent.created_at < cutoff)
        )
        self.db.commit()
        return int(res.rowcount or 0)

    def start_discovery_in_background(self) -> uuid.UUID:
        """Create the run row now and execute the cycle on a worker thread."""
        from acquisition.db.session import SessionLocal
        from acquisition.services.autonomous_automation_service import AutonomousAutomationService

        self.prune_old_events()
        row = AutonomousAutomationService(self.db, settings=self.settings).start_discovery_run_row()
        run_id = row.id
        bind = self.db.get_bind()

        def _work() -> None:
            from sqlalchemy.orm import Session as _Session

            with _Session(bind=bind) if bind is not None else SessionLocal() as db:
                svc = AutonomousAutomationService(db, settings=self.settings)
                run_row = db.get(AutonomousRunHistory, run_id)
                try:
                    svc.run_discovery_cycle(manual=True, run=run_row)
                except Exception as exc:  # noqa: BLE001
                    with live_trace.run(run_id, db):
                        live_trace.event("error", f"Run crashed: {exc}", level="error")
                    if run_row is not None:
                        run_row.status = "failed"
                        run_row.completed_at = datetime.now(timezone.utc)
                        run_row.error_summary = str(exc)[:2000]
                        db.add(run_row)
                        db.commit()

        threading.Thread(target=_work, name=f"discovery-{run_id}", daemon=True).start()
        return run_id

    def start_discovery_preview_in_background(self) -> uuid.UUID:
        """Create a preview run and execute harvest+triage only (no prospect writes)."""
        from acquisition.db.session import SessionLocal
        from acquisition.services.autonomous_automation_service import AutonomousAutomationService

        self.prune_old_events()
        row = AutonomousAutomationService(
            self.db, settings=self.settings
        ).start_discovery_preview_run_row()
        run_id = row.id
        bind = self.db.get_bind()

        def _work() -> None:
            from sqlalchemy.orm import Session as _Session

            with _Session(bind=bind) if bind is not None else SessionLocal() as db:
                svc = AutonomousAutomationService(db, settings=self.settings)
                run_row = db.get(AutonomousRunHistory, run_id)
                try:
                    svc.run_discovery_preview_cycle(run=run_row)
                except Exception as exc:  # noqa: BLE001
                    with live_trace.run(run_id, db):
                        live_trace.event("error", f"Preview crashed: {exc}", level="error")
                    if run_row is not None:
                        run_row.status = "failed"
                        run_row.completed_at = datetime.now(timezone.utc)
                        run_row.error_summary = str(exc)[:2000]
                        db.add(run_row)
                        db.commit()

        threading.Thread(target=_work, name=f"discovery-preview-{run_id}", daemon=True).start()
        return run_id

    def extract_surviving_pool(self, run_id: uuid.UUID) -> dict[str, Any]:
        """Load the exact surviving candidate pool snapshot for a prior run.

        Returns ok=False with a clear message when the full pool cannot be
        reconstructed (e.g. compacted runs that only stored partial accepted lists).
        """
        row = self.db.get(AutonomousRunHistory, run_id)
        if row is None:
            return {"ok": False, "error": "run_not_found", "message": "Run not found."}

        events = self.db.scalars(
            select(AcquisitionTraceEvent)
            .where(AcquisitionTraceEvent.run_id == run_id)
            .order_by(AcquisitionTraceEvent.seq.asc())
        ).all()

        surviving_expected: int | None = None
        pool: list[dict[str, Any]] | None = None
        old_selected: list[dict[str, Any]] = []
        old_sent_to_ai: int | None = None
        requested: int | None = None

        for ev in events:
            detail = ev.detail_json or {}
            title = ev.title or ""
            if isinstance(detail.get("surviving_pool"), list) and detail["surviving_pool"]:
                pool = list(detail["surviving_pool"])
            if detail.get("surviving_count") is not None:
                surviving_expected = int(detail["surviving_count"])
            elif detail.get("surviving") is not None:
                surviving_expected = int(detail["surviving"])
            elif detail.get("pool_before_cap") is not None:
                surviving_expected = int(detail["pool_before_cap"])
            elif detail.get("remaining") is not None and "Filtering summary" in title:
                surviving_expected = int(detail["remaining"])
            if detail.get("sent_to_ai") is not None and "Candidate pool" in title:
                old_sent_to_ai = int(detail["sent_to_ai"])
            if detail.get("requested") is not None and requested is None:
                requested = int(detail["requested"])
            if "AI triage result" in title and isinstance(detail.get("selected"), list):
                old_selected = [x for x in detail["selected"] if isinstance(x, dict)]

        if pool and surviving_expected is not None and len(pool) >= surviving_expected:
            return {
                "ok": True,
                "surviving_pool": pool[:surviving_expected],
                "surviving_count": surviving_expected,
                "old_selected": old_selected,
                "old_sent_to_ai": old_sent_to_ai,
                "requested": requested or 5,
                "source_run_id": str(run_id),
            }

        # Best-effort reconstruct from per-source accepted_companies (incomplete).
        accepted: list[dict[str, Any]] = []
        for ev in events:
            detail = ev.detail_json or {}
            rows = detail.get("accepted_companies")
            if isinstance(rows, list):
                for row_c in rows:
                    if isinstance(row_c, dict) and row_c.get("company"):
                        accepted.append(row_c)

        missing_bits = []
        if not pool:
            missing_bits.append(
                "no surviving_pool snapshot was persisted on this run "
                "(added after global-AI-selection; older/compacted runs lack it)"
            )
        elif surviving_expected is not None and len(pool) < surviving_expected:
            missing_bits.append(
                f"surviving_pool has {len(pool)} rows but surviving count was {surviving_expected}"
            )
        if surviving_expected and accepted and len(accepted) < surviving_expected:
            missing_bits.append(
                f"accepted_companies only preserved {len(accepted)} of {surviving_expected} survivors "
                "(display limit / compaction dropped the rest)"
            )
        if surviving_expected is None:
            missing_bits.append("surviving candidate count was not recorded")

        message = (
            f"Exact previous {surviving_expected or '?'}-candidate pool cannot be reconstructed because "
            + "; ".join(missing_bits or ["required pool metadata is missing"])
            + ". Re-running OpenAI web search would create a different pool, so replay was not started."
        )
        return {
            "ok": False,
            "error": "exact_pool_unavailable",
            "message": message,
            "surviving_expected": surviving_expected,
            "accepted_companies_found": len(accepted),
            "surviving_pool_found": len(pool or []),
            "old_selected": old_selected,
            "old_sent_to_ai": old_sent_to_ai,
            "requested": requested or 5,
            "source_run_id": str(run_id),
        }

    def replay_ai_selection(self, run_id: uuid.UUID) -> dict[str, Any]:
        """Re-run global AI selection against a stored surviving pool. Safe: no providers,
        no slice advances, no prospect/research/score/outreach writes.
        """
        from acquisition.services.discovery_candidate_triage_service import (
            DiscoveryCandidateTriageService,
            deserialize_candidate_from_pool,
        )
        from acquisition.services.icp_settings_service import IcpSettingsService

        extracted = self.extract_surviving_pool(run_id)
        if not extracted.get("ok"):
            return extracted

        pool_rows = extracted["surviving_pool"]
        requested = int(extracted.get("requested") or 5)
        candidates = [deserialize_candidate_from_pool(r) for r in pool_rows]
        icp = IcpSettingsService(self.db).as_dict()

        source_row = self.db.get(AutonomousRunHistory, run_id)
        assert source_row is not None
        replay = AutonomousRunHistory(
            cycle_type="discovery_selection_replay",
            status="running",
            started_at=datetime.now(timezone.utc),
            dry_run=True,
            summary_json={
                "replay_of": str(run_id),
                "surviving_count": len(candidates),
                "requested": requested,
            },
        )
        self.db.add(replay)
        self.db.commit()
        self.db.refresh(replay)

        with live_trace.run(replay.id, self.db):
            live_trace.event(
                "run",
                (
                    f"AI selection replay of run {run_id}: "
                    f"surviving={len(candidates)}, requested={requested} — "
                    "no providers, no prospect writes"
                ),
                source_run_id=str(run_id),
                surviving=len(candidates),
                requested=requested,
                replay=True,
            )
            live_trace.event(
                "plan",
                (
                    f"Candidate pool: surviving={len(candidates)}, "
                    f"sent_to_global_ai_comparison={len(candidates)}, "
                    f"requested_final={requested}"
                ),
                surviving=len(candidates),
                pool_before_cap=len(candidates),
                sent_to_ai=len(candidates),
                sent_to_global_ai_comparison=len(candidates),
                surviving_pool=pool_rows,
                surviving_count=len(candidates),
                requested=requested,
                replay=True,
            )
            triage = DiscoveryCandidateTriageService(
                self.db, settings=self.settings
            ).triage(icp=icp, candidates=candidates, select_count=requested)

            selected_items = [
                {
                    "rank": it.rank,
                    "company": it.candidate.company,
                    "domain": it.candidate.domain,
                    "source": it.candidate.discovery_source,
                    "state": it.candidate.state,
                    "category": it.candidate.industry,
                    "slice_key": it.candidate.slice_key,
                    "triage_score": it.triage_score,
                    "selection_score": it.triage_score,
                    "reason": it.reason,
                    "evidence": it.positive_signals,
                    "positive_signals": it.positive_signals,
                    "uncertainties": it.uncertainties,
                    "selected": True,
                    "candidate_id": it.candidate_id,
                }
                for it in triage.items
                if it.selected
            ]
            live_trace.event(
                "triage",
                (
                    f"AI candidate triage: model={triage.model or triage.source}, "
                    f"considered={triage.pool_size}, requested={requested}"
                ),
                model=triage.model,
                considered_count=triage.pool_size,
                pool_available=triage.pool_size,
                sent_to_ai=triage.pool_size,
                sent_to_global_ai_comparison=triage.pool_size,
                requested=requested,
                triage_source=triage.source,
                replay=True,
            )
            live_trace.event(
                "triage",
                (
                    f"AI triage result: considered={triage.pool_size}, "
                    f"selected={len(selected_items)}"
                ),
                level="success" if selected_items else "warning",
                model=triage.model,
                considered_count=triage.pool_size,
                pool_available=triage.pool_size,
                sent_to_ai=triage.pool_size,
                selected=selected_items,
                not_selected=[],
                selection_summary=triage.selection_summary,
                requested=requested,
                replay=True,
            )
            live_trace.event(
                "run",
                (
                    f"Replay finished: considered={triage.pool_size}, "
                    f"selected={len(selected_items)} — stopped (no research/outreach)"
                ),
                level="success",
                selected=selected_items,
                surviving=len(candidates),
                sent_to_global_ai_comparison=triage.pool_size,
                selected_count=len(selected_items),
                preview_stopped=True,
                replay=True,
            )

        old_names = {
            str(x.get("company") or "").strip().lower()
            for x in (extracted.get("old_selected") or [])
            if x.get("company")
        }
        new_names = {str(x.get("company") or "").strip().lower() for x in selected_items}
        comparison = []
        for old in extracted.get("old_selected") or []:
            name = str(old.get("company") or "").strip()
            comparison.append(
                {
                    "old": name,
                    "new": name if name.lower() in new_names else None,
                    "status": "same" if name.lower() in new_names else "replaced",
                }
            )
        for sel in selected_items:
            name = str(sel.get("company") or "").strip()
            if name.lower() not in old_names:
                comparison.append({"old": None, "new": name, "status": "new"})

        summary = {
            "replay_of": str(run_id),
            "surviving_count": len(candidates),
            "considered_count": triage.pool_size,
            "requested": requested,
            "model": triage.model,
            "triage_source": triage.source,
            "old_sent_to_ai": extracted.get("old_sent_to_ai"),
            "old_selected": extracted.get("old_selected"),
            "new_selected": selected_items,
            "selection_summary": triage.selection_summary,
            "comparison": comparison,
            "same_count": sum(1 for c in comparison if c["status"] == "same"),
            "changed_count": sum(1 for c in comparison if c["status"] in {"replaced", "new"}),
            "providers_called": False,
            "prospects_modified": False,
        }
        replay.status = "completed"
        replay.completed_at = datetime.now(timezone.utc)
        replay.discovered_count = 0
        replay.summary_json = summary
        self.db.add(replay)
        self.db.commit()

        return {
            "ok": True,
            "replay_run_id": str(replay.id),
            "source_run_id": str(run_id),
            **summary,
        }

    # ----- overview -----

    def overview(self) -> dict[str, Any]:
        from acquisition.services.acquisition_kpi_service import build_acquisition_kpis
        from acquisition.services.autonomous_automation_service import AutonomousAutomationService

        now = datetime.now(timezone.utc)
        ops = AutonomousAutomationService(self.db, settings=self.settings).operational_status()
        total = int(self.db.scalar(select(func.count()).select_from(Prospect)) or 0)
        status_counts = {
            (k.value if hasattr(k, "value") else str(k)): int(v)
            for k, v in self.db.execute(
                select(Prospect.acquisition_status, func.count()).group_by(Prospect.acquisition_status)
            ).all()
        }
        # Count all prospects added (CSV / manual / any source) — not discovery-engine only.
        seen_col = func.coalesce(Prospect.first_seen_at, Prospect.discovered_at, Prospect.created_at)
        since = (now - timedelta(days=13)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_col = func.date_trunc(literal_column("'day'"), seen_col).label("day")
        daily_rows = self.db.execute(
            select(day_col, func.count())
            .where(seen_col >= since)
            .group_by(literal_column("day"))
        ).all()
        daily_map = {(_as_utc(d) or now).date(): int(c) for d, c in daily_rows}
        days = []
        for i in range(14):
            day = (since + timedelta(days=i)).date()
            days.append({"label": day.strftime("%b %d"), "short": day.strftime("%d"), "count": daily_map.get(day, 0)})
        peak = max([d["count"] for d in days] + [1])
        for d in days:
            d["pct"] = round(d["count"] / peak * 100)
        new_7d = sum(d["count"] for d in days[-7:])
        prev_7d = sum(d["count"] for d in days[:7])

        # SVG area-chart coordinates (viewBox 560×180)
        chart_w, chart_h, pad_x, pad_t = 536.0, 134.0, 12.0, 18.0
        n_days = max(len(days) - 1, 1)
        for i, d in enumerate(days):
            d["x"] = round(pad_x + chart_w * i / n_days, 2)
            d["y"] = round(pad_t + chart_h * (1 - (d["count"] / peak if peak else 0)), 2)
        area_line = " ".join(f'{d["x"]},{d["y"]}' for d in days)
        area_poly = f"{pad_x},{pad_t + chart_h} {area_line} {pad_x + chart_w},{pad_t + chart_h}"

        by_source = []
        src_col = func.coalesce(Prospect.discovery_source, Prospect.source, literal_column("'manual'")).label("src")
        for src, cnt, ready in self.db.execute(
            select(
                src_col,
                func.count(),
                func.count().filter(Prospect.acquisition_status == AcquisitionStatus.READY_FOR_OUTREACH),
            ).group_by(literal_column("src"))
        ).all():
            by_source.append(
                {
                    "source": src,
                    "label": SOURCE_LABELS.get(src, src.replace("_", " ").title() if src else "Manual"),
                    "count": int(cnt),
                    "ready": int(ready or 0),
                }
            )
        by_source.sort(key=lambda r: -r["count"])
        src_peak = max([s["count"] for s in by_source] + [1])
        for s in by_source:
            s["pct"] = round(s["count"] / src_peak * 100) if src_peak else 0
            s["share"] = round(s["count"] / total * 100) if total else 0

        kpis = build_acquisition_kpis(self.db)["totals"]
        ready = status_counts.get("ready_for_outreach", 0)
        funnel_raw = [
            ("In database", total),
            ("Ready for outreach", ready),
            ("Reached", int(kpis.get("prospects_reached") or 0)),
            ("Assessment started", int(kpis.get("assessment_started") or 0)),
            ("Qualified", int(kpis.get("qualified") or 0)),
            ("Booked", int(kpis.get("booked") or 0)),
            ("Purchased", int(kpis.get("purchased") or 0)),
        ]
        top = max(funnel_raw[0][1], 1)
        funnel = [{"label": l, "count": c, "pct": max(2 if c else 0, round(c / top * 100))} for l, c in funnel_raw]

        health = self.import_pipeline_health(total=total, ready=ready)
        return {
            "ops": ops,
            "total": total,
            "ready": ready,
            "new_7d": new_7d,
            "prev_7d": prev_7d,
            "needs_research": status_counts.get("needs_more_research", 0),
            "excluded": status_counts.get("excluded", 0),
            "status_counts": status_counts,
            "days": days,
            "by_source": by_source,
            "funnel": funnel,
            "area_line": area_line,
            "area_poly": area_poly,
            "revenue": float(kpis.get("revenue") or 0),
            "runs": [],
            "health": health,
            "running": False,
        }

    def import_pipeline_health(self, *, total: int, ready: int) -> list[dict[str, str]]:
        """Health for CSV / manual prospect workflow (no discovery engine)."""
        items: list[dict[str, str]] = []
        if total == 0:
            items.append(
                {
                    "level": "warn",
                    "text": "No prospects yet. Import a CSV or create a prospect to get started.",
                }
            )
        elif ready == 0:
            items.append(
                {
                    "level": "warn",
                    "text": "No prospects are ready for outreach yet. Approve imported prospects under Prospects.",
                }
            )
        else:
            items.append(
                {
                    "level": "ok",
                    "text": f"{ready} prospect{'s' if ready != 1 else ''} ready for outreach.",
                }
            )
        return items

    def health(self, ops: dict[str, Any], runs: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Legacy discovery-engine health (kept for tests / Live Trace)."""
        items: list[dict[str, str]] = []
        provider = ops.get("provider") or {}
        if not ops.get("discovery_enabled"):
            items.append({"level": "warn", "text": "Scheduled discovery is off. Turn it on in Engine & AI → Automation."})
        if not provider.get("configured"):
            items.append({"level": "bad", "text": f"Discovery provider not ready: {provider.get('message') or 'configure a source'}."})
        research = ops.get("research_provider") or {}
        if research and not research.get("configured"):
            items.append({"level": "warn", "text": f"Research provider not ready: {research.get('message')}"})
        completed = [r for r in runs if r["status"] in {"completed", "failed"}]
        if completed and completed[0]["status"] == "failed":
            items.append({"level": "bad", "text": f"Last discovery run failed: {(completed[0].get('error') or '')[:140]}"})
        last_two = [r for r in completed if r["status"] == "completed"][:2]
        if len(last_two) == 2 and all((r.get("new") or 0) == 0 for r in last_two):
            items.append({"level": "bad", "text": "No new companies in the last 2 runs — check sources, slices or ICP rules."})
        if runs and runs[0]["status"] == "completed" and (runs[0].get("new") or 0) > 0:
            items.append({"level": "ok", "text": f"Last run added {runs[0]['new']} new companies ({runs[0]['started_ago']})."})
        if not runs:
            items.append({"level": "warn", "text": "No discovery run yet. Use Live Trace → Run discovery now."})
        if ops.get("provider_mode") == "mock":
            items.append({"level": "warn", "text": "Using mock discovery data (development mode)."})
        return items
