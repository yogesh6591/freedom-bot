"""Prospect domain service.

Manual CSV/XLSX import remains an Admin utility only — it is not required for
autonomous acquisition. Permanent Prospect records are channel-neutral and may
originate from future discovery, research, or manual entry.
"""

from __future__ import annotations

import csv
import uuid
from datetime import datetime, timezone
from io import StringIO

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from acquisition.models.prospect import AcquisitionStatus, ApprovalStatus, Prospect
from acquisition.schemas.prospect import ProspectCreate, ProspectUpdate
from acquisition.services.prospect_identity import domain_from_website_or_email

LINKEDIN_EXPORT_HEADERS = (
    "Company",
    "Website",
    "Executive Name",
    "Executive Title",
    "LinkedIn URL",
    "Email",
)

STRUCTURED_EXPORT_HEADERS = (
    "Company",
    "Website",
    "Domain",
    "City",
    "State",
    "Region",
    "Industry",
    "Company Description",
    "Executive Name",
    "Executive Title",
    "Authority Type",
    "Founder Or Equivalent",
    "LinkedIn URL",
    "Email",
    "Email Confidence",
    "Company Phone",
    "Other Contact Path",
    "Revenue Band",
    "Headcount Band",
    "Fit Note",
    "Research Notes",
    "Fit Score",
    "Timing Score",
    "Reachability Score",
    "Priority Score",
    "Score Confidence",
    "Acquisition Status",
    "Approval Status",
    "Source",
    "Discovered At",
    "Last Researched At",
)


class ProspectNotFoundError(Exception):
    def __init__(self, prospect_id: uuid.UUID) -> None:
        self.prospect_id = prospect_id
        super().__init__(f"Prospect {prospect_id} not found")


class ProspectService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_prospects(
        self,
        *,
        search: str | None = None,
        approval_status: ApprovalStatus | None = None,
        exclude_approved: bool = False,
        source: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Prospect], int]:
        filters = []
        if approval_status is not None:
            filters.append(Prospect.approval_status == approval_status)
        elif exclude_approved:
            filters.append(Prospect.approval_status != ApprovalStatus.APPROVED)
        if source:
            filters.append(Prospect.source.ilike(f"%{source.strip()}%"))
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    Prospect.company.ilike(pattern),
                    Prospect.executive_name.ilike(pattern),
                    Prospect.email.ilike(pattern),
                    Prospect.source.ilike(pattern),
                    Prospect.executive_title.ilike(pattern),
                    Prospect.domain.ilike(pattern),
                    Prospect.industry.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(Prospect)
        list_stmt = select(Prospect)
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)

        total = int(self.db.scalar(count_stmt) or 0)
        rows = list(
            self.db.scalars(
                list_stmt.order_by(Prospect.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def count_by_approval(self) -> dict[str, int]:
        """Return counts for inbox (non-approved) and approved tabs."""
        approved = int(
            self.db.scalar(
                select(func.count())
                .select_from(Prospect)
                .where(Prospect.approval_status == ApprovalStatus.APPROVED)
            )
            or 0
        )
        total = int(self.db.scalar(select(func.count()).select_from(Prospect)) or 0)
        return {"inbox": max(0, total - approved), "approved": approved}

    def bulk_approve(self, prospect_ids: list[uuid.UUID]) -> int:
        """Mark the given prospects approved. Returns how many were updated."""
        if not prospect_ids:
            return 0
        rows = list(
            self.db.scalars(select(Prospect).where(Prospect.id.in_(prospect_ids))).all()
        )
        updated = 0
        for prospect in rows:
            if prospect.approval_status != ApprovalStatus.APPROVED:
                prospect.approval_status = ApprovalStatus.APPROVED
                self.db.add(prospect)
                updated += 1
        if updated:
            self.db.commit()
        return updated

    def bulk_unapprove(self, prospect_ids: list[uuid.UUID]) -> int:
        """Move approved prospects back to pending. Returns how many were updated."""
        if not prospect_ids:
            return 0
        rows = list(
            self.db.scalars(select(Prospect).where(Prospect.id.in_(prospect_ids))).all()
        )
        updated = 0
        for prospect in rows:
            if prospect.approval_status == ApprovalStatus.APPROVED:
                prospect.approval_status = ApprovalStatus.PENDING
                self.db.add(prospect)
                updated += 1
        if updated:
            self.db.commit()
        return updated

    def bulk_delete(self, prospect_ids: list[uuid.UUID]) -> int:
        """Delete the given prospects. Returns how many were deleted."""
        if not prospect_ids:
            return 0
        rows = list(
            self.db.scalars(select(Prospect).where(Prospect.id.in_(prospect_ids))).all()
        )
        deleted = 0
        for prospect in rows:
            self.db.delete(prospect)
            deleted += 1
        if deleted:
            self.db.commit()
        return deleted

    def import_prospects_from_rows(
        self,
        rows: list[dict[str, str]],
        *,
        default_source: str = "csv_import",
    ) -> dict[str, object]:
        """Admin utility: create prospects from spreadsheet rows.

        Import is optional migration/testing support — not required for the
        autonomous acquisition engine to operate.
        """
        from pydantic import ValidationError

        created = 0
        errors: list[str] = []
        for index, row in enumerate(rows, start=2):  # 1-based header → data from row 2
            try:
                payload = ProspectCreate.model_validate(
                    {
                        "company": row.get("company") or "",
                        "website": row.get("website") or None,
                        "linkedin_url": row.get("linkedin_url") or None,
                        "executive_name": row.get("executive_name") or "",
                        "executive_title": row.get("executive_title") or None,
                        "email": row.get("email") or "",
                        "revenue_band": row.get("revenue_band") or None,
                        "headcount_band": row.get("headcount_band") or None,
                        "fit_note": row.get("fit_note") or None,
                        "source": row.get("source") or default_source,
                        "approval_status": ApprovalStatus.PENDING,
                    }
                )
                self.create_prospect(payload)
                created += 1
            except ValidationError as exc:
                first = exc.errors()[0] if exc.errors() else {}
                field = ".".join(str(p) for p in first.get("loc", ())) or "row"
                msg = first.get("msg", "Invalid value")
                errors.append(f"Row {index}: {field}: {msg}")
            except Exception as exc:  # noqa: BLE001 — collect per-row errors
                errors.append(f"Row {index}: {exc}")
        return {"created": created, "errors": errors[:25], "error_count": len(errors)}

    def list_approved_for_linkedin_export(self) -> list[Prospect]:
        """All approved prospects for Jeanne's LinkedIn CSV — not paginated."""
        stmt = (
            select(Prospect)
            .where(Prospect.approval_status == ApprovalStatus.APPROVED)
            .order_by(Prospect.company.asc(), Prospect.executive_name.asc())
        )
        return list(self.db.scalars(stmt).all())

    def build_linkedin_export_csv(self) -> str:
        """UTF-8 CSV of approved prospects (LinkedIn-compatible columns only)."""
        prospects = self.list_approved_for_linkedin_export()
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(LINKEDIN_EXPORT_HEADERS)
        for prospect in prospects:
            writer.writerow(
                [
                    prospect.company or "",
                    prospect.website or "",
                    prospect.executive_name or "",
                    prospect.executive_title or "",
                    prospect.linkedin_url or "",
                    prospect.email or "",
                ]
            )
        return buffer.getvalue()

    def build_structured_export_csv(self) -> str:
        """UTF-8 CSV of all prospects with autonomous-acquisition structured fields."""
        prospects = list(
            self.db.scalars(
                select(Prospect).order_by(Prospect.company.asc(), Prospect.executive_name.asc())
            ).all()
        )
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(STRUCTURED_EXPORT_HEADERS)
        for prospect in prospects:
            writer.writerow(
                [
                    prospect.company or "",
                    prospect.website or "",
                    prospect.domain or "",
                    prospect.city or "",
                    prospect.state or "",
                    prospect.region or "",
                    prospect.industry or "",
                    prospect.company_description or "",
                    prospect.executive_name or "",
                    prospect.executive_title or "",
                    prospect.authority_type or "",
                    (
                        ""
                        if prospect.is_founder_or_equivalent is None
                        else ("yes" if prospect.is_founder_or_equivalent else "no")
                    ),
                    prospect.linkedin_url or "",
                    prospect.email or "",
                    prospect.email_confidence or "",
                    prospect.company_phone or "",
                    prospect.other_contact_path or "",
                    prospect.revenue_band or "",
                    prospect.headcount_band or "",
                    prospect.fit_note or "",
                    prospect.research_notes or "",
                    "" if prospect.fit_score is None else prospect.fit_score,
                    "" if prospect.timing_score is None else prospect.timing_score,
                    "" if prospect.reachability_score is None else prospect.reachability_score,
                    "" if prospect.priority_score is None else prospect.priority_score,
                    "" if prospect.score_confidence is None else prospect.score_confidence,
                    (
                        prospect.acquisition_status.value
                        if prospect.acquisition_status is not None
                        else ""
                    ),
                    prospect.approval_status.value if prospect.approval_status else "",
                    prospect.source or "",
                    prospect.discovered_at.isoformat() if prospect.discovered_at else "",
                    (
                        prospect.last_researched_at.isoformat()
                        if prospect.last_researched_at
                        else ""
                    ),
                ]
            )
        return buffer.getvalue()

    def get_prospect(self, prospect_id: uuid.UUID) -> Prospect:
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise ProspectNotFoundError(prospect_id)
        return prospect

    def create_prospect(self, payload: ProspectCreate) -> Prospect:
        now = datetime.now(timezone.utc)
        email = str(payload.email).lower()
        domain = domain_from_website_or_email(website=payload.website, email=email)
        data = payload.model_dump()
        acquisition_status = data.pop("acquisition_status", None) or AcquisitionStatus.DISCOVERED
        prospect = Prospect(
            **{
                k: v
                for k, v in data.items()
                if k
                not in {
                    "email",
                    "approval_status",
                }
            },
            email=email,
            domain=domain,
            approval_status=payload.approval_status,
            acquisition_status=acquisition_status,
            discovered_at=now,
        )
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)
        return prospect

    def update_prospect(self, prospect_id: uuid.UUID, payload: ProspectUpdate) -> Prospect:
        prospect = self.get_prospect(prospect_id)
        data = payload.model_dump(exclude_unset=True)
        if "email" in data and data["email"] is not None:
            data["email"] = str(data["email"]).lower()
        for key, value in data.items():
            setattr(prospect, key, value)
        # Keep normalized domain in sync when website/email change.
        if "website" in data or "email" in data:
            prospect.domain = domain_from_website_or_email(
                website=prospect.website, email=prospect.email
            )
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)
        return prospect

    def delete_prospect(self, prospect_id: uuid.UUID) -> None:
        prospect = self.get_prospect(prospect_id)
        self.db.delete(prospect)
        self.db.commit()

    def distinct_sources(self) -> list[str]:
        stmt = (
            select(Prospect.source)
            .where(Prospect.source.is_not(None), Prospect.source != "")
            .distinct()
            .order_by(Prospect.source)
        )
        return [value for value in self.db.scalars(stmt).all() if value]
