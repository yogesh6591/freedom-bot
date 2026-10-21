"""Permanent prospect database listing + CSV/XLSX export (no Instantly dependency)."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.models.assessment import AssessmentProfile
from acquisition.models.campaign import CampaignProspect
from acquisition.models.prospect import Prospect


EXPORT_HEADERS = [
    "company",
    "website",
    "domain",
    "city",
    "state",
    "region",
    "industry",
    "decision_maker",
    "title",
    "email",
    "email_confidence",
    "company_phone",
    "other_contact_path",
    "linkedin_url",
    "revenue_band",
    "headcount_band",
    "fit_score",
    "timing_score",
    "reachability_score",
    "priority_score",
    "learning_adjustment",
    "effective_priority_score",
    "score_confidence",
    "fit_reasons",
    "timing_reasons",
    "reachability_reasons",
    "priority_reasons",
    "learning_adjustment_reasons",
    "public_signals",
    "research_notes",
    "source_urls",
    "source_types",
    "provenance_notes",
    "date_discovered",
    "date_last_refreshed",
    "acquisition_status",
    "outreach_status",
    "reply_status",
    "assessment_status",
    "qualification_status",
    "meeting_status",
    "purchase_status",
    "revenue_amount",
    "campaign_history",
    "discovery_source",
    "discovery_engine_source",
    "npi",
    "business_type",
    "times_seen",
    "first_seen_at",
    "last_seen_at",
    "website_verified",
]


@dataclass(slots=True)
class ProspectDatabaseFilters:
    acquisition_status: str | None = None
    industry: str | None = None
    state: str | None = None
    region: str | None = None
    outreach_status: str | None = None
    assessment_status: str | None = None
    qualification_status: str | None = None
    meeting_status: str | None = None
    purchase_status: str | None = None
    min_priority: float | None = None
    max_priority: float | None = None
    q: str | None = None


class ProspectDatabaseService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_enriched(
        self,
        *,
        filters: ProspectDatabaseFilters | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = filters or ProspectDatabaseFilters()
        rows = self._load_prospects()
        enriched = [self._enrich(p) for p in rows]
        filtered = [r for r in enriched if self._matches(r, filters)]
        total = len(filtered)
        page = filtered[offset : offset + max(1, limit)]
        return page, total

    def get_enriched(self, prospect_id) -> dict[str, Any] | None:
        prospect = self.db.scalar(
            select(Prospect)
            .options(selectinload(Prospect.research_sources))
            .where(Prospect.id == prospect_id)
        )
        if prospect is None:
            return None
        return self._enrich(prospect)

    def build_csv(self, *, filters: ProspectDatabaseFilters | None = None) -> str:
        rows, _ = self.list_enriched(filters=filters, limit=50_000, offset=0)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=EXPORT_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: self._cell(row.get(k)) for k in EXPORT_HEADERS})
        return buffer.getvalue()

    def build_xlsx_bytes(self, *, filters: ProspectDatabaseFilters | None = None) -> bytes:
        rows, _ = self.list_enriched(filters=filters, limit=50_000, offset=0)
        wb = Workbook()
        ws = wb.active
        ws.title = "Prospects"
        ws.append(EXPORT_HEADERS)
        for row in rows:
            ws.append([self._cell(row.get(k)) for k in EXPORT_HEADERS])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def _load_prospects(self) -> list[Prospect]:
        return list(
            self.db.scalars(
                select(Prospect)
                .options(selectinload(Prospect.research_sources))
                .order_by(Prospect.company.asc(), Prospect.executive_name.asc())
            ).all()
        )

    def _enrich(self, prospect: Prospect) -> dict[str, Any]:
        links = list(
            self.db.scalars(
                select(CampaignProspect)
                .options(selectinload(CampaignProspect.campaign))
                .where(CampaignProspect.prospect_id == prospect.id)
                .order_by(CampaignProspect.created_at.desc())
            ).all()
        )
        profiles = {
            p.campaign_prospect_id: p
            for p in self.db.scalars(
                select(AssessmentProfile).where(
                    AssessmentProfile.campaign_prospect_id.in_([lnk.id for lnk in links])
                )
            ).all()
        } if links else {}

        latest = links[0] if links else None
        latest_profile = profiles.get(latest.id) if latest else None
        history = []
        for link in links:
            history.append(
                {
                    "campaign": link.campaign.name if link.campaign else str(link.campaign_id),
                    "outreach_status": (
                        link.outreach_status.value if link.outreach_status else ""
                    ),
                    "assessment_status": (
                        link.assessment_status.value if link.assessment_status else ""
                    ),
                    "booking_status": link.booking_status or "",
                    "purchase_outcome": (
                        link.purchase_outcome.value if link.purchase_outcome else ""
                    ),
                    "message_key": link.outreach_message_key or "",
                }
            )

        sources = list(prospect.research_sources or [])
        source_urls = [s.source_url for s in sources if s.source_url]
        source_types = [s.source_type for s in sources if s.source_type]
        provenance = [
            f"{s.category or s.source_type}: {(s.note or '')[:120]}"
            for s in sources[:8]
        ]

        reply_status = "none"
        if latest and latest.last_reply_at:
            reply_status = "replied"

        meeting_status = "none"
        if latest:
            if latest.booked_at or (latest.booking_status or "").lower() == "confirmed":
                meeting_status = latest.booking_status or "booked"
            if latest.consultation_show_status:
                meeting_status = (
                    latest.consultation_show_status.value
                    if hasattr(latest.consultation_show_status, "value")
                    else str(latest.consultation_show_status)
                )

        purchase_status = (
            latest.purchase_outcome.value
            if latest and latest.purchase_outcome
            else "unknown"
        )
        qual_status = (
            latest_profile.qualification_status.value
            if latest_profile and latest_profile.qualification_status
            else ""
        )

        effective = prospect.effective_priority_score
        if effective is None and prospect.priority_score is not None:
            effective = prospect.priority_score

        return {
            "id": str(prospect.id),
            "company": prospect.company,
            "website": prospect.website,
            "domain": prospect.domain,
            "city": prospect.city,
            "state": prospect.state,
            "region": prospect.region,
            "industry": prospect.industry,
            "decision_maker": prospect.executive_name,
            "title": prospect.executive_title,
            "email": prospect.email,
            "email_confidence": prospect.email_confidence,
            "company_phone": prospect.company_phone,
            "other_contact_path": prospect.other_contact_path,
            "linkedin_url": prospect.linkedin_url,
            "revenue_band": prospect.revenue_band,
            "headcount_band": prospect.headcount_band,
            "fit_score": prospect.fit_score,
            "timing_score": prospect.timing_score,
            "reachability_score": prospect.reachability_score,
            "priority_score": prospect.priority_score,
            "learning_adjustment": prospect.learning_adjustment,
            "effective_priority_score": effective,
            "score_confidence": prospect.score_confidence,
            "fit_reasons": prospect.fit_reasons,
            "timing_reasons": prospect.timing_reasons,
            "reachability_reasons": prospect.reachability_reasons,
            "priority_reasons": prospect.priority_reasons,
            "learning_adjustment_reasons": prospect.learning_adjustment_reasons,
            "public_signals": prospect.public_signals,
            "research_notes": prospect.research_notes,
            "classification_json": prospect.classification_json,
            "agentic_research": (prospect.classification_json or {}).get("agentic_research")
            if isinstance(prospect.classification_json, dict)
            else None,
            "discovery_provider_label": (
                (prospect.classification_json or {}).get("discovery_provider")
                if isinstance(prospect.classification_json, dict)
                else None
            )
            or prospect.discovery_source,
            "source_urls": source_urls,
            "source_types": source_types,
            "provenance_notes": provenance,
            "research_sources": [
                {
                    "url": s.source_url,
                    "type": s.source_type,
                    "category": s.category,
                    "note": s.note,
                    "confidence": s.confidence,
                    "checked_at": s.checked_at.isoformat() if s.checked_at else None,
                }
                for s in sources
            ],
            "date_discovered": (
                prospect.discovered_at.isoformat() if prospect.discovered_at else None
            ),
            "date_last_refreshed": (
                prospect.last_researched_at.isoformat()
                if prospect.last_researched_at
                else None
            ),
            "acquisition_status": (
                prospect.acquisition_status.value if prospect.acquisition_status else ""
            ),
            "outreach_status": (
                latest.outreach_status.value
                if latest and latest.outreach_status
                else "none"
            ),
            "reply_status": reply_status,
            "assessment_status": (
                latest.assessment_status.value
                if latest and latest.assessment_status
                else "not_started"
            ),
            "qualification_status": qual_status or "pending",
            "meeting_status": meeting_status,
            "purchase_status": purchase_status,
            "revenue_amount": float(latest.revenue_amount)
            if latest and latest.revenue_amount is not None
            else None,
            "campaign_history": history,
            "discovery_source": prospect.source,
            "discovery_engine_source": prospect.discovery_source,
            "discovery_slice_key": prospect.discovery_slice_key,
            "npi": prospect.npi,
            "business_type": prospect.business_type,
            "times_seen": prospect.times_seen,
            "first_seen_at": prospect.first_seen_at.isoformat() if prospect.first_seen_at else None,
            "last_seen_at": prospect.last_seen_at.isoformat() if prospect.last_seen_at else None,
            "website_verified": prospect.website_verified,
            "discovered_at_dt": prospect.discovered_at,
            "last_researched_at_dt": prospect.last_researched_at,
        }

    def _matches(self, row: dict[str, Any], filters: ProspectDatabaseFilters) -> bool:
        def eq(field: str, wanted: str | None) -> bool:
            if not wanted:
                return True
            return str(row.get(field) or "").lower() == wanted.strip().lower()

        if not eq("acquisition_status", filters.acquisition_status):
            return False
        if filters.industry and filters.industry.lower() not in str(
            row.get("industry") or ""
        ).lower():
            return False
        if not eq("state", filters.state):
            return False
        if filters.region and filters.region.lower() not in str(
            row.get("region") or ""
        ).lower():
            return False
        if not eq("outreach_status", filters.outreach_status):
            return False
        if not eq("assessment_status", filters.assessment_status):
            return False
        if not eq("qualification_status", filters.qualification_status):
            return False
        if not eq("meeting_status", filters.meeting_status):
            return False
        if not eq("purchase_status", filters.purchase_status):
            return False
        pri = row.get("effective_priority_score")
        if pri is None:
            pri = row.get("priority_score")
        if filters.min_priority is not None and (
            pri is None or float(pri) < filters.min_priority
        ):
            return False
        if filters.max_priority is not None and (
            pri is None or float(pri) > filters.max_priority
        ):
            return False
        if filters.q:
            blob = " ".join(
                str(row.get(k) or "")
                for k in (
                    "company",
                    "decision_maker",
                    "email",
                    "industry",
                    "domain",
                    "state",
                )
            ).lower()
            if filters.q.strip().lower() not in blob:
                return False
        return True

    @staticmethod
    def _cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, datetime):
            return value.isoformat()
        return value
