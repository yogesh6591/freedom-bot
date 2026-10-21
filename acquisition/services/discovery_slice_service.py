"""Slice ledger — split the market into small searchable slices and rotate them.

A slice is source × category × state × angle.
Each run picks the least-recently-run slices, so discovery keeps moving across
the country and across angles instead of asking the same question every day.
Slices that stop producing new companies rest for a while; productive ones
come back sooner. The ledger rebuilds itself from the current ICP rules, so a
new priority category in Admin becomes new slices automatically.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from acquisition.integrations.discovery.sources.base import US_STATES, SliceSpec, state_code
from acquisition.models.prospect import DiscoverySlice, Prospect

# Canonical buckets keep "psychology", "behavioral health" and "group practice"
# from becoming three copies of the same search.
BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("behavioral health group practices", ("psycholog", "behavioral", "behavioural", "mental", "group practice", "counsel", "therap")),
    ("fractional executive firms", ("fractional",)),
    ("management consulting firms", ("consult",)),
    ("business advisory firms", ("advis",)),
    ("marketing and creative agencies", ("agenc",)),
)

ANGLES: dict[str, tuple[str | None, ...]] = {
    "openai_web": (
        "founder-led",
        "lesser-known independent",
        "recently expanded or hiring",
        "award-winning or recognized",
        "B2B-focused",
    ),
}

REST_DAYS_NO_YIELD = 14
REST_DAYS_EXHAUSTED = 60


def bucket_for(category: str) -> str:
    c = (category or "").strip().lower()
    for bucket, needles in BUCKETS:
        if any(n in c for n in needles):
            return bucket
    return (category or "").strip() or "professional services firms"


def buckets_from_icp(icp: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for cat in icp.get("priority_categories") or []:
        b = bucket_for(str(cat))
        if b and b not in seen:
            seen.append(b)
    return seen or ["management consulting firms", "marketing and creative agencies"]


def slice_key(source: str, category: str, state: str | None, city: str | None, angle: str | None) -> str:
    return "|".join([source, category, state or "", city or "", angle or ""])[:255]


def region_prefix(key: str | None) -> str | None:
    """source|category|state — used to find companies already found nearby."""
    if not key:
        return None
    parts = key.split("|")
    return "|".join(parts[:3]) if len(parts) >= 3 else key


class DiscoverySliceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ----- universe -----

    def desired_slices(self, icp: dict[str, Any], sources: Iterable[str]) -> list[dict[str, Any]]:
        states = list(US_STATES.keys())
        rows: list[dict[str, Any]] = []
        sources = list(sources)
        for bucket in buckets_from_icp(icp):
            for source in sources:
                for st in states:
                    for angle in ANGLES.get(source, (None,)):
                        rows.append(dict(source=source, category=bucket, state=st, city=None, angle=angle))
        for r in rows:
            r["key"] = slice_key(r["source"], r["category"], r["state"], r["city"], r["angle"])
        return rows

    def ensure_universe(self, icp: dict[str, Any], sources: Iterable[str]) -> int:
        rows = self.desired_slices(icp, sources)
        if not rows:
            return 0
        before = int(self.db.scalar(select(func.count()).select_from(DiscoverySlice)) or 0)
        payload = [{**r, "id": uuid.uuid4()} for r in rows]
        for i in range(0, len(payload), 500):
            stmt = pg_insert(DiscoverySlice).values(payload[i : i + 500]).on_conflict_do_nothing(
                index_elements=["key"]
            )
            self.db.execute(stmt)
        self.db.commit()
        after = int(self.db.scalar(select(func.count()).select_from(DiscoverySlice)) or 0)
        return after - before

    # ----- picking -----

    def pick(
        self,
        source: str,
        *,
        count: int,
        categories: list[str] | None = None,
        priority_states: list[str] | None = None,
        now: datetime | None = None,
    ) -> list[DiscoverySlice]:
        now = now or datetime.now(timezone.utc)
        stmt = select(DiscoverySlice).where(
            DiscoverySlice.source == source,
            DiscoverySlice.exhausted.is_(False),
            (DiscoverySlice.rest_until.is_(None)) | (DiscoverySlice.rest_until <= now),
        )
        if categories:
            stmt = stmt.where(DiscoverySlice.category.in_(categories))
        pri = [state_code(s) for s in (priority_states or []) if state_code(s)]
        order = []
        if pri:
            order.append(case((DiscoverySlice.state.in_(pri), 0), else_=1))
        order += [
            DiscoverySlice.last_run_at.is_not(None),
            DiscoverySlice.last_run_at.asc(),
            func.random(),
        ]
        # Take a wider window then shuffle lightly so categories interleave.
        window = list(self.db.scalars(stmt.order_by(*order).limit(max(count * 3, count))).all())
        never_run = [s for s in window if s.last_run_at is None]
        rest = [s for s in window if s.last_run_at is not None]
        random.shuffle(never_run)
        chosen: list[DiscoverySlice] = []
        seen_cats: dict[str, int] = {}
        for s in never_run + rest:
            if len(chosen) >= count:
                break
            # Soft balance across categories within one pick.
            if seen_cats.get(s.category, 0) > count // max(1, len(categories or [s.category])) + 1:
                continue
            seen_cats[s.category] = seen_cats.get(s.category, 0) + 1
            chosen.append(s)
        return chosen

    def to_spec(self, row: DiscoverySlice, *, exclude_domains: list[str] | None = None) -> SliceSpec:
        return SliceSpec(
            key=row.key,
            source=row.source,
            category=row.category,
            state=row.state,
            city=row.city,
            angle=row.angle,
            cursor=row.cursor or 0,
            exclude_domains=list(exclude_domains or []),
        )

    def known_domains_near(self, key: str, *, limit: int = 60) -> list[str]:
        prefix = region_prefix(key)
        if not prefix:
            return []
        parts = prefix.split("|")
        category, state = (parts[1], parts[2]) if len(parts) >= 3 else (None, None)
        stmt = select(Prospect.domain).where(Prospect.domain.is_not(None))
        if state:
            stmt = stmt.where(Prospect.state == state)
        if category:
            stmt = stmt.where(Prospect.discovery_slice_key.like(f"%|{category}|{state or ''}|%"))
        rows = self.db.scalars(stmt.order_by(Prospect.created_at.desc()).limit(limit)).all()
        return [r for r in rows if r]

    # ----- bookkeeping -----

    def record_harvest(
        self,
        row: DiscoverySlice,
        *,
        candidates: int,
        next_cursor: int | None,
        exhausted: bool,
        error: str | None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        row.runs = (row.runs or 0) + 1
        row.last_run_at = now
        row.candidates_total = (row.candidates_total or 0) + int(candidates)
        row.last_error = (error or None) and str(error)[:500]
        if next_cursor is not None:
            row.cursor = int(next_cursor)
        if exhausted:
            row.cursor = 0
            row.rest_until = now + timedelta(days=REST_DAYS_EXHAUSTED)
        self.db.add(row)
        self.db.commit()

    def record_yield(self, key: str, new_count: int, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        row = self.db.scalar(select(DiscoverySlice).where(DiscoverySlice.key == key))
        if row is None:
            return
        row.new_total = (row.new_total or 0) + int(new_count)
        row.last_new = int(new_count)
        if new_count <= 0 and (row.runs or 0) >= 2:
            row.rest_until = now + timedelta(days=REST_DAYS_NO_YIELD)
        self.db.add(row)
        self.db.commit()

    def summary(self) -> dict[str, Any]:
        rows = self.db.execute(
            select(
                DiscoverySlice.source,
                func.count(),
                func.count(DiscoverySlice.last_run_at),
                func.coalesce(func.sum(DiscoverySlice.new_total), 0),
                func.coalesce(func.sum(DiscoverySlice.candidates_total), 0),
            ).group_by(DiscoverySlice.source)
        ).all()
        return {
            r[0]: {"slices": r[1], "run": r[2], "new_total": int(r[3]), "candidates_total": int(r[4])}
            for r in rows
        }
