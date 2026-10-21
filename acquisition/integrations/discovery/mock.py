"""Deterministic mock discovery/research providers for tests and local Admin runs.

No live internet. Returns US-nationwide fixtures (not Chicago-only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from acquisition.integrations.discovery.base import DiscoveryProviderError
from acquisition.integrations.discovery.types import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    ContactPathInfo,
    DecisionMakerInfo,
    DiscoveryCandidate,
    EvidenceItem,
    ProviderStatus,
    PublicResearchResult,
    PublicSignal,
)
from acquisition.services import acquisition_trace as acq_trace

# US-wide fixtures — intentionally multi-metro, never Chicago-only.
_FIXTURE_COMPANIES: list[dict[str, Any]] = [
    {
        "company": "Summit Advisory Partners",
        "website": "https://summitadvisory.example",
        "domain": "summitadvisory.example",
        "city": "Austin",
        "state": "TX",
        "region": "South",
        "industry": "advisory consultants",
        "source_url": "https://search.example/q=summit+advisory+partners",
        "description": "Expertise-driven management advisory firm serving mid-market clients.",
        "revenue_band": "$8–20M",
        "headcount_band": "40-120",
        "decision_maker": {
            "name": "Jordan Hale",
            "title": "Founder & CEO",
            "authority_type": "founder",
            "is_founder_or_equivalent": True,
            "linkedin_url": "https://linkedin.com/in/jordan-hale-example",
        },
        "contact": {
            "email": "jordan@summitadvisory.example",
            "email_confidence": "medium",
            "company_phone": "+1-512-555-0142",
        },
        "signals": [
            "Company currently lists 6 open consulting roles.",
            "Recent hiring may indicate growth/capacity pressure.",
        ],
    },
    {
        "company": "Northwind Behavioral Group",
        "website": "https://northwindbh.example",
        "domain": "northwindbh.example",
        "city": "Denver",
        "state": "CO",
        "region": "West",
        "industry": "behavioral health group practice",
        "source_url": "https://search.example/q=northwind+behavioral+group",
        "description": "Multi-site psychology and behavioral-health group practice.",
        "revenue_band": "$7–15M",
        "headcount_band": "80-200",
        "decision_maker": {
            "name": "Dr. Priya Shah",
            "title": "Owner / Clinical Director",
            "authority_type": "owner",
            "is_founder_or_equivalent": True,
            "linkedin_url": None,
        },
        "contact": {
            "email": "priya@northwindbh.example",
            "email_confidence": "high",
            "company_phone": "+1-303-555-0199",
        },
        "signals": [
            "Public notice of a new Denver clinic location opening.",
            "Hiring activity visible for licensed clinicians.",
        ],
    },
    {
        "company": "Cascade Fractional Ops",
        "website": "https://cascadefractional.example",
        "domain": "cascadefractional.example",
        "city": "Seattle",
        "state": "WA",
        "region": "West",
        "industry": "fractional CXO",
        "source_url": "https://search.example/q=cascade+fractional+ops",
        "description": "Fractional CXO and operations advisory for scaling white-collar firms.",
        "revenue_band": "$5–12M",
        "headcount_band": "15-60",
        "decision_maker": {
            "name": "Morgan Lee",
            "title": "Managing Partner",
            "authority_type": "managing partner",
            "is_founder_or_equivalent": True,
            "linkedin_url": "https://linkedin.com/in/morgan-lee-example",
        },
        "contact": {
            "email": "morgan@cascadefractional.example",
            "email_confidence": "medium",
            "company_phone": None,
        },
        "signals": [
            "Press release announcing a new service line for AI operations.",
            "Public discussion of scaling advisory delivery.",
        ],
    },
    {
        "company": "Harbor Creative Agency",
        "website": "https://harborcreative.example",
        "domain": "harborcreative.example",
        "city": "Atlanta",
        "state": "GA",
        "region": "Southeast",
        "industry": "agencies",
        "source_url": "https://search.example/q=harbor+creative+agency",
        "description": "Full-service marketing agency with knowledge-worker density.",
        "revenue_band": "$10–25M",
        "headcount_band": "50-150",
        "decision_maker": {
            "name": "Alex Rivera",
            "title": "CEO",
            "authority_type": "ceo",
            "is_founder_or_equivalent": True,
            "linkedin_url": None,
        },
        "contact": {
            "email": None,  # intentional: research may leave contact unresolved
            "email_confidence": None,
            "company_phone": "+1-404-555-0110",
        },
        "signals": [
            "Job board shows repeated openings for account strategists.",
        ],
    },
    {
        "company": "Lakeside Retail Shops",
        "website": "https://lakesideretail.example",
        "domain": "lakesideretail.example",
        "city": "Chicago",
        "state": "IL",
        "region": "Midwest",
        "industry": "retail",
        "source_url": "https://search.example/q=lakeside+retail+shops",
        "description": "Regional retail chain — typically excluded by ICP.",
        "revenue_band": "$3–8M",
        "headcount_band": "100-300",
        "decision_maker": {
            "name": "Sam Quinn",
            "title": "Owner",
            "authority_type": "owner",
            "is_founder_or_equivalent": True,
            "linkedin_url": None,
        },
        "contact": {
            "email": "sam@lakesideretail.example",
            "email_confidence": "low",
            "company_phone": None,
        },
        "signals": [],
        "excluded_fixture": True,
    },
]


class MockDiscoveryProvider:
    """In-memory discovery — uses ICP filters; geography is US-wide."""

    name = "mock_discovery"

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            configured=True,
            provider_name=self.name,
            message="Deterministic mock discovery (no live network).",
        )

    def search_companies(
        self,
        *,
        icp: dict[str, Any],
        limit: int = 5,
    ) -> list[DiscoveryCandidate]:
        if self._fail:
            raise DiscoveryProviderError("mock discovery forced failure", provider=self.name)
        geo = icp.get("geography") or {}
        if not (geo.get("nationwide") or "United States" in (geo.get("countries") or [])):
            # Still allow results but providers should respect ICP; empty if non-US.
            return []
        excluded = [str(x).lower() for x in (icp.get("excluded_categories") or [])]
        priority = [str(x).lower() for x in (icp.get("priority_categories") or [])]
        query = "mock fixtures: ICP priority categories × US nationwide"
        acq_trace.trace_query(index=1, query=query, reason="deterministic mock provider")
        acq_trace.trace_search(
            provider=self.name, query=query, raw_results=len(_FIXTURE_COMPANIES)
        )
        out: list[DiscoveryCandidate] = []
        now = datetime.now(timezone.utc)
        for row in _FIXTURE_COMPANIES:
            industry = str(row.get("industry") or "").lower()
            company = str(row["company"])
            if any(ex and ex in industry for ex in excluded):
                hit = next(ex for ex in excluded if ex and ex in industry)
                acq_trace.trace_candidate(
                    company=company,
                    decision="REJECT",
                    reasons=[f"excluded category: {hit}"],
                )
                continue
            # Prefer priority matches; still allow other non-excluded if space remains.
            is_priority = any(p and p in industry for p in priority) if priority else True
            if not is_priority and len(out) >= max(1, limit // 2):
                acq_trace.trace_candidate(
                    company=company,
                    decision="REJECT",
                    reasons=["not priority category; fixture quota filled"],
                )
                continue
            reasons = ["US geography matched"]
            if is_priority:
                reasons.append("priority category matched")
            else:
                reasons.append("plausible ICP fit (not excluded)")
            acq_trace.trace_search_result(
                company=company,
                domain=row.get("domain"),
                url=row.get("website") or row.get("source_url"),
                title=company,
                snippet=row.get("industry"),
            )
            acq_trace.trace_candidate(company=company, decision="ACCEPT", reasons=reasons)
            out.append(
                DiscoveryCandidate(
                    company=row["company"],
                    website=row.get("website"),
                    domain=row.get("domain"),
                    city=row.get("city"),
                    state=row.get("state"),
                    region=row.get("region"),
                    industry=row.get("industry"),
                    source="autonomous_discovery",
                    source_url=row.get("source_url"),
                    source_type="search",
                    discovered_at=now,
                    notes="Mock public search candidate (US nationwide).",
                )
            )
            if len(out) >= limit:
                break
        return out

    def search_company_context(
        self,
        *,
        company: str,
        domain: str | None = None,
        website: str | None = None,
        icp: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        del icp, website
        wanted = (company or "").strip().lower()
        domain_n = (domain or "").strip().lower()
        matches = []
        for row in _FIXTURE_COMPANIES:
            if wanted and wanted in str(row["company"]).lower():
                matches.append(row)
            elif domain_n and domain_n == str(row.get("domain") or "").lower():
                matches.append(row)
        return [
            DiscoveryCandidate(
                company=r["company"],
                website=r.get("website"),
                domain=r.get("domain"),
                city=r.get("city"),
                state=r.get("state"),
                region=r.get("region"),
                industry=r.get("industry"),
                source="autonomous_discovery",
                source_url=r.get("source_url"),
                source_type="search",
            )
            for r in matches
        ]


class MockResearchProvider:
    """Returns structured public context from the same fixture set."""

    name = "mock_research"

    def __init__(self, *, fail: bool = False, omit_contact: bool = False) -> None:
        self._fail = fail
        self._omit_contact = omit_contact

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            configured=True,
            provider_name=self.name,
            message="Deterministic mock research (no live network).",
        )

    def fetch_public_context(
        self,
        *,
        company: str,
        website: str | None = None,
        domain: str | None = None,
        industry: str | None = None,
        icp: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> PublicResearchResult:
        del icp, industry, existing
        if self._fail:
            raise DiscoveryProviderError("mock research forced failure", provider=self.name)

        row = self._match(company=company, website=website, domain=domain)
        if row is None:
            return PublicResearchResult(
                company_name=company,
                website=website,
                domain=domain,
                research_notes="No public mock fixture matched; no facts invented.",
                partial=True,
                provider_errors=["mock_research: no fixture match"],
            )

        dm_raw = row.get("decision_maker") or {}
        contact_raw = dict(row.get("contact") or {})
        if self._omit_contact:
            contact_raw = {"email": None, "email_confidence": None, "company_phone": None}

        evidence: list[EvidenceItem] = [
            EvidenceItem(
                source_url=row.get("source_url"),
                source_type="search",
                category="company",
                note=f"Discovered via mock public search: {row['company']}",
                confidence=CONFIDENCE_HIGH,
            ),
            EvidenceItem(
                source_url=row.get("website"),
                source_type="company_site",
                category="company",
                note=row.get("description") or "Company site context",
                confidence=CONFIDENCE_MEDIUM,
            ),
        ]
        dm = DecisionMakerInfo(
            name=dm_raw.get("name"),
            title=dm_raw.get("title"),
            authority_type=dm_raw.get("authority_type"),
            is_founder_or_equivalent=dm_raw.get("is_founder_or_equivalent"),
            linkedin_url=dm_raw.get("linkedin_url"),
            confidence=CONFIDENCE_MEDIUM,
            evidence=[
                EvidenceItem(
                    source_url=row.get("website"),
                    source_type="leadership_page",
                    category="decision_maker",
                    note=f"Public leadership reference: {dm_raw.get('name')} ({dm_raw.get('title')})",
                    confidence=CONFIDENCE_MEDIUM,
                )
            ],
        )
        contact = ContactPathInfo(
            email=contact_raw.get("email"),
            email_confidence=contact_raw.get("email_confidence"),
            company_phone=contact_raw.get("company_phone"),
            other_contact_path=contact_raw.get("other_contact_path"),
            evidence=[
                EvidenceItem(
                    source_url=row.get("website"),
                    source_type="company_site",
                    category="contact",
                    note=(
                        f"Business email listed publicly: {contact_raw.get('email')}"
                        if contact_raw.get("email")
                        else "No legitimate business email found in public mock sources."
                    ),
                    confidence=CONFIDENCE_MEDIUM if contact_raw.get("email") else CONFIDENCE_LOW,
                )
            ],
        )
        signals = [
            PublicSignal(
                text=text,
                category="timing",
                confidence=CONFIDENCE_MEDIUM,
                evidence=[
                    EvidenceItem(
                        source_url=row.get("website") or row.get("source_url"),
                        source_type="news",
                        category="timing",
                        note=text,
                        confidence=CONFIDENCE_MEDIUM,
                    )
                ],
            )
            for text in (row.get("signals") or [])
        ]
        return PublicResearchResult(
            company_name=row["company"],
            website=row.get("website"),
            domain=row.get("domain"),
            city=row.get("city"),
            state=row.get("state"),
            region=row.get("region"),
            industry=row.get("industry"),
            company_description=row.get("description"),
            revenue_band=row.get("revenue_band"),
            headcount_band=row.get("headcount_band"),
            locations=[{"city": row.get("city"), "state": row.get("state")}],
            decision_maker=dm,
            contact=contact,
            signals=signals,
            research_notes="Mock public research applied. Signals are hypotheses, not proven problems.",
            evidence=evidence,
            partial=False,
        )

    def _match(
        self,
        *,
        company: str,
        website: str | None,
        domain: str | None,
    ) -> dict[str, Any] | None:
        company_l = (company or "").strip().lower()
        domain_l = (domain or "").strip().lower()
        website_l = (website or "").strip().lower()
        for row in _FIXTURE_COMPANIES:
            if domain_l and domain_l == str(row.get("domain") or "").lower():
                return row
            if website_l and website_l.rstrip("/") == str(row.get("website") or "").lower().rstrip("/"):
                return row
            if company_l and company_l == str(row["company"]).lower():
                return row
        return None


class NullDiscoveryProvider:
    """Explicit unconfigured discovery — returns no candidates."""

    name = "none"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            configured=False,
            provider_name=self.name,
            message=(
                "No live discovery provider configured. Set DISCOVERY_PROVIDER=mock "
                "for local testing, or plug in a permitted public search provider later."
            ),
        )

    def search_companies(self, *, icp: dict[str, Any], limit: int = 5) -> list[DiscoveryCandidate]:
        del icp, limit
        return []

    def search_company_context(
        self,
        *,
        company: str,
        domain: str | None = None,
        website: str | None = None,
        icp: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        del company, domain, website, icp
        return []


class NullResearchProvider:
    """Explicit unconfigured research — invents nothing."""

    name = "none"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            configured=False,
            provider_name=self.name,
            message="No live research provider configured. Use DISCOVERY_PROVIDER=mock for fixtures.",
        )

    def fetch_public_context(
        self,
        *,
        company: str,
        website: str | None = None,
        domain: str | None = None,
        industry: str | None = None,
        icp: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> PublicResearchResult:
        del industry, icp, existing
        return PublicResearchResult(
            company_name=company,
            website=website,
            domain=domain,
            research_notes="Research provider not configured; no facts invented.",
            partial=True,
            provider_errors=["research_provider_not_configured"],
        )
