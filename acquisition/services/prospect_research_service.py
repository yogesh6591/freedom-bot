"""Write/read research evidence (provenance) for prospects."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.prospect import Prospect, ProspectResearchSource
from acquisition.services.prospect_service import ProspectNotFoundError


class ProspectResearchService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_source(
        self,
        prospect_id: uuid.UUID,
        *,
        source_url: str | None = None,
        source_type: str = "other",
        category: str | None = None,
        note: str | None = None,
        confidence: float | None = None,
        checked_at: datetime | None = None,
    ) -> ProspectResearchSource:
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise ProspectNotFoundError(prospect_id)
        row = ProspectResearchSource(
            prospect_id=prospect_id,
            source_url=(source_url or "").strip() or None,
            source_type=(source_type or "other").strip() or "other",
            category=(category or "").strip() or None,
            note=(note or "").strip() or None,
            confidence=confidence,
            checked_at=checked_at or datetime.now(timezone.utc),
        )
        prospect.last_researched_at = row.checked_at
        self.db.add(row)
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(row)
        return row

    def add_or_update_source(
        self,
        prospect_id: uuid.UUID,
        *,
        source_url: str | None = None,
        source_type: str = "other",
        category: str | None = None,
        note: str | None = None,
        confidence: float | None = None,
        checked_at: datetime | None = None,
    ) -> ProspectResearchSource:
        """Upsert evidence for the same prospect + URL + category."""
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise ProspectNotFoundError(prospect_id)

        url = (source_url or "").strip() or None
        cat = (category or "").strip() or None
        existing: ProspectResearchSource | None = None
        if url:
            stmt = select(ProspectResearchSource).where(
                ProspectResearchSource.prospect_id == prospect_id,
                ProspectResearchSource.source_url == url,
            )
            if cat:
                stmt = stmt.where(ProspectResearchSource.category == cat)
            else:
                stmt = stmt.where(ProspectResearchSource.category.is_(None))
            existing = self.db.scalar(stmt.limit(1))

        now = checked_at or datetime.now(timezone.utc)
        if existing is not None:
            existing.source_type = (source_type or existing.source_type or "other").strip() or "other"
            if note is not None:
                existing.note = (note or "").strip() or None
            if confidence is not None:
                existing.confidence = confidence
            existing.checked_at = now
            prospect.last_researched_at = now
            self.db.add(existing)
            self.db.add(prospect)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        return self.add_source(
            prospect_id,
            source_url=url,
            source_type=source_type,
            category=cat,
            note=note,
            confidence=confidence,
            checked_at=now,
        )

    def list_sources(self, prospect_id: uuid.UUID) -> list[ProspectResearchSource]:
        return list(
            self.db.scalars(
                select(ProspectResearchSource)
                .where(ProspectResearchSource.prospect_id == prospect_id)
                .order_by(ProspectResearchSource.checked_at.desc())
            ).all()
        )

    def delete_source(self, source_id: uuid.UUID) -> bool:
        row = self.db.get(ProspectResearchSource, source_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def delete_sources(self, source_ids: list[uuid.UUID]) -> int:
        if not source_ids:
            return 0
        removed = 0
        for sid in source_ids:
            row = self.db.get(ProspectResearchSource, sid)
            if row is None:
                continue
            self.db.delete(row)
            removed += 1
        if removed:
            self.db.commit()
        return removed
