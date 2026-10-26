"""Read models for the Admin Overview dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.prospect import AcquisitionStatus, Prospect

SOURCE_LABELS = {
    "openai_web": "OpenAI web search",
    "nppes": "NPPES registry",
    "exa": "Exa company index",
    "legacy_search": "Legacy search",
    "mock": "Mock (dev)",
    "manual": "Manual",
    "csv": "CSV import",
}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AcquisitionLiveService:
    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def overview(self) -> dict[str, Any]:
        from acquisition.services.acquisition_kpi_service import build_acquisition_kpis

        now = datetime.now(timezone.utc)
        total = int(self.db.scalar(select(func.count()).select_from(Prospect)) or 0)
        status_counts = {
            (k.value if hasattr(k, "value") else str(k)): int(v)
            for k, v in self.db.execute(
                select(Prospect.acquisition_status, func.count()).group_by(Prospect.acquisition_status)
            ).all()
        }
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
            "ops": {},
            "total": total,
            "ready": ready,
            "new_7d": sum(d["count"] for d in days[-7:]),
            "prev_7d": sum(d["count"] for d in days[:7]),
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
