"""AutonomousProspectDiscoveryService — ICP-driven company discovery (no Instantly push)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.base import DiscoveryProvider, DiscoveryProviderError
from acquisition.integrations.discovery.factory import resolve_discovery_provider
from acquisition.integrations.discovery.types import DiscoveryCandidate
from acquisition.models.prospect import AcquisitionStatus, ApprovalStatus, Prospect
from acquisition.schemas.prospect import ProspectCreate, ProspectUpdate
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.prospect_identity import (
    domain_from_website_or_email,
    find_existing_company_or_prospect,
    normalize_company_name,
    normalize_domain,
    placeholder_email_for_domain,
)
from acquisition.services.prospect_research_service import ProspectResearchService
from acquisition.services.prospect_service import ProspectService
from acquisition.services import acquisition_trace as acq_trace

logger = logging.getLogger(__name__)

UNKNOWN_DECISION_MAKER = "Unknown decision-maker"


@dataclass(slots=True)
class DiscoveryPersistResult:
    prospect_id: uuid.UUID
    created: bool
    company: str
    domain: str | None


class AutonomousProspectDiscoveryService:
    """Discover US companies from configured ICP + public-source providers."""

    def __init__(
        self,
        db: Session,
        *,
        provider: DiscoveryProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.provider = provider or resolve_discovery_provider(self.settings, db=db)
        self.icp = IcpSettingsService(db)
        self.prospects = ProspectService(db)
        self.evidence = ProspectResearchService(db)

    def provider_status(self) -> dict[str, Any]:
        status = self.provider.status()
        return {
            "configured": status.configured,
            "provider_name": status.provider_name,
            "message": status.message,
        }

    def discover_candidates(
        self, *, limit: int | None = None, preview: bool = False
    ) -> list[DiscoveryCandidate]:
        icp = self.icp.as_dict()
        cap = self._effective_limit(limit)
        acq_trace.trace_icp(icp)
        geo = icp.get("geography") or {}
        if not (geo.get("nationwide") or "United States" in (geo.get("countries") or [])):
            logger.info("discovery skipped: ICP geography is not US nationwide")
            if acq_trace.is_acquisition_trace_enabled(self.settings):
                acq_trace.trace_candidate(
                    company="(none)",
                    decision="REJECT",
                    reasons=["outside target geography: ICP is not US nationwide"],
                )
            return []
        # Explicitly do not restrict to Chicago / a single metro.
        search_kwargs: dict[str, Any] = {"icp": icp, "limit": cap}
        if preview and getattr(self.provider, "name", "") == "multi_source_discovery":
            search_kwargs["preview"] = True
        candidates = self.provider.search_companies(**search_kwargs)
        return candidates[:cap]

    def upsert_candidate(self, candidate: DiscoveryCandidate) -> DiscoveryPersistResult:
        domain = normalize_domain(candidate.domain) or domain_from_website_or_email(
            website=candidate.website, email=None
        )
        existing = None
        if candidate.npi:
            from sqlalchemy import select as _select

            existing = self.db.scalar(
                _select(Prospect).where(Prospect.npi == candidate.npi).limit(1)
            )
        if existing is None:
            existing = find_existing_company_or_prospect(
                self.db,
                website=candidate.website,
                domain=domain,
                company=candidate.company,
                executive_name=None,
            )
        match_key = acq_trace.diagnose_identity_match(
            email=None,
            website=candidate.website,
            domain=domain,
            company=candidate.company,
            executive_name=None,
            existing=existing,
        )
        acq_trace.trace_dedupe(
            company=candidate.company,
            normalized_company=normalize_company_name(candidate.company),
            domain=domain,
            decision_maker=None,
            existing_match=existing is not None,
            match_key=match_key,
            action="UPDATE EXISTING" if existing is not None else "CREATE NEW",
        )
        now = candidate.discovered_at or datetime.now(timezone.utc)
        if existing is not None:
            # Prefer resolved brand over a generic service-page label.
            from acquisition.services.company_identity import (
                is_generic_service_title,
                prefer_canonical_company_name,
            )

            preferred = prefer_canonical_company_name(
                current=existing.company, candidate=candidate.company
            )
            if preferred and not is_generic_service_title(preferred):
                existing.company = preferred
            elif candidate.company and (
                not existing.company or is_generic_service_title(existing.company)
            ):
                existing.company = candidate.company
            update = ProspectUpdate(
                website=candidate.website or existing.website,
                city=candidate.city or existing.city,
                state=candidate.state or existing.state,
                region=candidate.region or existing.region,
                industry=candidate.industry or existing.industry,
                source=candidate.source or existing.source or "autonomous_discovery",
            )
            data = update.model_dump(exclude_unset=True)
            for key, value in data.items():
                if value is not None:
                    setattr(existing, key, value)
            if domain and not existing.domain:
                existing.domain = domain
            if existing.discovered_at is None:
                existing.discovered_at = now
            if existing.acquisition_status == AcquisitionStatus.DISCOVERED or existing.acquisition_status is None:
                existing.acquisition_status = AcquisitionStatus.DISCOVERED
            if candidate.identity_uncertain:
                existing.acquisition_status = AcquisitionStatus.NEEDS_MORE_RESEARCH
            self._apply_v2_fields(existing, candidate, domain=domain, created=False, now=now)
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            self._record_discovery_evidence(existing, candidate)
            acq_trace.trace_save(
                prospect_id=existing.id,
                company=existing.company,
                domain=existing.domain,
                city=existing.city,
                state=existing.state,
                status=(
                    existing.acquisition_status.value
                    if existing.acquisition_status
                    else None
                ),
                action="updated",
            )
            return DiscoveryPersistResult(
                prospect_id=existing.id,
                created=False,
                company=existing.company,
                domain=existing.domain,
            )

        email = placeholder_email_for_domain(domain)
        payload = ProspectCreate(
            company=candidate.company.strip(),
            website=candidate.website,
            executive_name=UNKNOWN_DECISION_MAKER,
            executive_title=None,
            email=email,  # type: ignore[arg-type]  # placeholder is syntactically valid
            city=candidate.city,
            state=candidate.state,
            region=candidate.region,
            industry=candidate.industry,
            source=candidate.source or "autonomous_discovery",
            email_confidence="placeholder",
            acquisition_status=AcquisitionStatus.DISCOVERED,
            approval_status=ApprovalStatus.PENDING,
        )
        # Bypass EmailStr strictness for .invalid placeholders via ORM path when needed.
        try:
            prospect = self.prospects.create_prospect(payload)
        except Exception:  # noqa: BLE001
            prospect = self._create_prospect_orm(candidate, domain=domain, email=email, now=now)

        # Ensure placeholder metadata. Never derive a company domain from the
        # placeholder email (registry records have no website yet).
        prospect.email_confidence = "placeholder"
        prospect.domain = domain
        prospect.discovered_at = now
        if candidate.identity_uncertain:
            prospect.acquisition_status = AcquisitionStatus.NEEDS_MORE_RESEARCH
        else:
            prospect.acquisition_status = AcquisitionStatus.DISCOVERED
        self._apply_v2_fields(prospect, candidate, domain=domain, created=True, now=now)
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)
        self._record_discovery_evidence(prospect, candidate)
        acq_trace.trace_save(
            prospect_id=prospect.id,
            company=prospect.company,
            domain=prospect.domain,
            city=prospect.city,
            state=prospect.state,
            status=(
                prospect.acquisition_status.value if prospect.acquisition_status else None
            ),
            action="created",
        )
        return DiscoveryPersistResult(
            prospect_id=prospect.id,
            created=True,
            company=prospect.company,
            domain=prospect.domain,
        )

    def run_discovery_cycle(self, *, limit: int | None = None) -> dict[str, Any]:
        """Discover up to `limit` candidates and upsert Prospects. No Instantly push."""
        cap = self._effective_limit(limit)
        status = self.provider_status()
        if not status["configured"]:
            return {
                "ok": False,
                "provider": status,
                "limit": cap,
                "candidates": 0,
                "created": 0,
                "updated": 0,
                "errors": ["discovery_provider_configuration_required"],
                "results": [],
            }

        try:
            candidates = self.discover_candidates(limit=cap)
        except DiscoveryProviderError as exc:
            return {
                "ok": False,
                "provider": status,
                "limit": cap,
                "candidates": 0,
                "created": 0,
                "updated": 0,
                "errors": [str(exc)],
                "results": [],
            }
        created = 0
        updated = 0
        errors: list[str] = []
        results: list[dict[str, Any]] = []
        for candidate in candidates[:cap]:
            try:
                result = self.upsert_candidate(candidate)
                if result.created:
                    created += 1
                else:
                    updated += 1
                results.append(
                    {
                        "prospect_id": str(result.prospect_id),
                        "created": result.created,
                        "company": result.company,
                        "domain": result.domain,
                    }
                )
            except Exception as exc:  # noqa: BLE001 — isolate per-candidate failures
                logger.exception("failed to upsert discovery candidate %s", candidate.company)
                errors.append(f"{candidate.company}: {exc}")

        return {
            "ok": True,
            "provider": status,
            "limit": cap,
            "candidates": len(candidates),
            "created": created,
            "updated": updated,
            "errors": errors,
            "results": results,
            "icp_geography": self.icp.as_dict().get("geography"),
        }

    def _effective_limit(self, limit: int | None) -> int:
        icp_limit = int(self.icp.as_dict().get("discovery_cycle_limit") or 0)
        configured = icp_limit or int(self.settings.discovery_cycle_limit or 5)
        hard_max = int(self.settings.discovery_cycle_hard_max or 25)
        requested = configured if limit is None else int(limit)
        return max(1, min(requested, hard_max))

    def _apply_v2_fields(
        self,
        prospect: Prospect,
        candidate: DiscoveryCandidate,
        *,
        domain: str | None,
        created: bool,
        now: datetime,
    ) -> None:
        """Provenance + registry facts from multi-source discovery (no-op for legacy)."""
        prospect.last_seen_at = now
        prospect.times_seen = int(prospect.times_seen or 0) + 1
        if created or not prospect.first_seen_at:
            prospect.first_seen_at = prospect.first_seen_at or now
        if candidate.discovery_source and (created or not prospect.discovery_source):
            prospect.discovery_source = candidate.discovery_source
        if candidate.slice_key and (created or not prospect.discovery_slice_key):
            prospect.discovery_slice_key = candidate.slice_key
        if candidate.npi and not prospect.npi:
            prospect.npi = candidate.npi
        if candidate.raw.get("website_verified"):
            prospect.website_verified = True
        if candidate.company_phone and not prospect.company_phone:
            prospect.company_phone = candidate.company_phone
        if candidate.description and not prospect.company_description:
            prospect.company_description = candidate.description
        if candidate.headcount_hint and not prospect.headcount_band:
            prospect.headcount_band = f"est. {candidate.headcount_hint} staff (index)"
        if candidate.revenue_hint_usd and not prospect.revenue_band:
            prospect.revenue_band = f"~${candidate.revenue_hint_usd / 1e6:.0f}M (index)"
        # Registry-listed authorized official (NPPES) seeds the decision-maker.
        unknown = (prospect.executive_name or "").strip().lower() in {"", UNKNOWN_DECISION_MAKER.lower()}
        if candidate.executive_name and unknown:
            prospect.executive_name = candidate.executive_name
            prospect.executive_title = candidate.executive_title or prospect.executive_title
            title = (candidate.executive_title or "").lower()
            if any(k in title for k in ("owner", "ceo", "president", "founder", "principal", "director", "partner")):
                prospect.is_founder_or_equivalent = True
                prospect.authority_type = "registry_authorized_official"

    def _record_discovery_evidence(self, prospect: Prospect, candidate: DiscoveryCandidate) -> None:
        if not candidate.source_url and not candidate.notes:
            return
        self.evidence.add_or_update_source(
            prospect.id,
            source_url=candidate.source_url,
            source_type=candidate.source_type or "search",
            category="discovery",
            note=candidate.notes or f"Discovered company candidate: {candidate.company}",
            confidence=0.9 if candidate.discovery_source == "nppes" else 0.7,
        )
        if candidate.executive_name and candidate.discovery_source == "nppes":
            self.evidence.add_or_update_source(
                prospect.id,
                source_url=candidate.source_url,
                source_type="registry",
                category="decision_maker",
                note=(
                    f"{candidate.company}: authorized official {candidate.executive_name}"
                    f"{', ' + candidate.executive_title if candidate.executive_title else ''} "
                    f"(NPPES NPI {candidate.npi})"
                ),
                confidence=0.85,
            )

    def _create_prospect_orm(
        self,
        candidate: DiscoveryCandidate,
        *,
        domain: str | None,
        email: str,
        now: datetime,
    ) -> Prospect:
        prospect = Prospect(
            company=candidate.company.strip(),
            website=candidate.website,
            domain=domain,
            city=candidate.city,
            state=candidate.state,
            region=candidate.region,
            industry=candidate.industry,
            executive_name=UNKNOWN_DECISION_MAKER,
            email=email,
            email_confidence="placeholder",
            source=candidate.source or "autonomous_discovery",
            approval_status=ApprovalStatus.PENDING,
            acquisition_status=AcquisitionStatus.DISCOVERED,
            discovered_at=now,
        )
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)
        return prospect
