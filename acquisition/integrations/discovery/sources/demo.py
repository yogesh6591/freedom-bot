"""Offline demo harvesters (DISCOVERY_DEMO_MODE=true, non-production only).

Produce deterministic, realistic-looking candidates per slice — including the
junk a real search returns (directories, "Top 10" pages, excluded industries,
repeats) — so developers can watch the full pipeline in Live Trace without API
keys or network access. Never enabled in production.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone

from acquisition.integrations.discovery.sources.base import REGION_BY_STATE, HarvestResult, SliceSpec
from acquisition.integrations.discovery.types import DiscoveryCandidate

_FIRST = ["Summit", "Northwind", "Cascade", "Harbor", "Beacon", "Keystone", "Meridian", "Ridgeline", "Lakeshore", "Evergreen", "Granite", "Bluebird", "Copperline", "Silverleaf", "Oakmont", "Brightpath", "Westbrook", "Clearwater"]
_SUFFIX = {
    "behavioral health group practices": ["Behavioral Health Group", "Psychological Associates", "Counseling Center", "Therapy Collective", "Family Services"],
    "fractional executive firms": ["Fractional CFO Partners", "Fractional Executives", "CFO Advisors", "Operating Partners"],
    "management consulting firms": ["Consulting Group", "Management Consulting", "Strategy Partners", "Advisory Group"],
    "business advisory firms": ["Advisory", "Business Advisors", "Growth Advisory"],
    "marketing and creative agencies": ["Creative Agency", "Digital", "Brand Studio", "Marketing Group"],
}
_OWNERS = [("Dana", "Whitfield"), ("Marcus", "Hale"), ("Priya", "Shah"), ("Jordan", "Reyes"), ("Elena", "Brooks"), ("Sam", "Okafor"), ("Lauren", "Kim"), ("Victor", "Nguyen")]
_TITLES = ["Founder & CEO", "Owner", "Managing Partner", "President", "Clinical Director / Owner"]
_JUNK = [
    ("Top 10 {cat} in {place} — 2026 Rankings", "clutch.co"),
    ("Best {cat} Near {place} | Reviews", "yelp.com"),
    ("{first} Law Firm", "{slug}law.com"),
    ("{first} Accounting Firm", "{slug}cpa.com"),
]


def _rng(key: str) -> random.Random:
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))


class DemoHarvester:
    def __init__(self, name: str, label: str) -> None:
        self.name = name
        self.label = f"{label} (demo data)"

    def configured(self) -> bool:
        return True

    def status_message(self) -> str:
        return "Offline demo data — development only."

    def supports(self, category: str) -> bool:
        return True

    def harvest(self, spec: SliceSpec, *, limit: int, excluded: list[str] | None = None) -> HarvestResult:
        rng = _rng(spec.key + f"|{spec.cursor}")
        suffixes = _SUFFIX.get(spec.category, ["Group"])
        out: list[DiscoveryCandidate] = []
        n = rng.randint(5, 9)
        for i in range(n):
            first = rng.choice(_FIRST)
            if rng.random() < 0.22:
                title_t, dom_t = rng.choice(_JUNK)
                slug = first.lower()
                name = title_t.format(cat=spec.category, place=spec.place, first=first)
                domain = dom_t.format(slug=slug)
            else:
                name = f"{first} {rng.choice(suffixes)}"
                # Shared pool so later slices produce "already known" repeats.
                domain = f"{first.lower()}{rng.choice(suffixes).split()[0].lower()}.com"
            fn, ln = rng.choice(_OWNERS)
            is_nppes = self.name == "nppes"
            npi = str(1_000_000_000 + (int(hashlib.md5(name.encode()).hexdigest()[:7], 16) % 899_999_999)) if is_nppes else None
            out.append(
                DiscoveryCandidate(
                    company=name,
                    website=None if is_nppes else f"https://{domain}",
                    domain=None if is_nppes else domain,
                    city=rng.choice(["Austin", "Denver", "Columbus", "Raleigh", "Phoenix", "Tampa", "Nashville", "Madison"]),
                    state=spec.state,
                    region=REGION_BY_STATE.get(spec.state or ""),
                    industry=spec.category,
                    source="autonomous_discovery",
                    source_url=(f"https://npiregistry.cms.hhs.gov/provider-view/{npi}" if is_nppes else f"https://{domain}/about"),
                    source_type="registry" if is_nppes else "company_index",
                    discovered_at=datetime.now(timezone.utc),
                    notes=f"Demo {self.name} candidate for {spec.category} in {spec.place}.",
                    identity_confidence=0.8,
                    discovery_source=self.name,
                    slice_key=spec.key,
                    npi=npi,
                    executive_name=f"{fn} {ln}" if is_nppes else None,
                    executive_title=rng.choice(_TITLES) if is_nppes else None,
                    company_phone=f"({rng.randint(200, 989)}) 555-{rng.randint(1000, 9999)}" if is_nppes else None,
                    headcount_hint=None if is_nppes else rng.choice([18, 32, 45, 60, 85, 120, 240]),
                    description=f"{name} — {spec.category} serving clients across {spec.place}.",
                )
            )
        return HarvestResult(
            candidates=out[: max(limit, 1)],
            raw_count=n,
            next_cursor=(spec.cursor + 200) if self.name == "nppes" else None,
            exhausted=False,
            cost_usd=0.0 if self.name == "nppes" else round(rng.uniform(0.004, 0.03), 4),
            request={"demo": True, "slice": spec.key},
        )


class DemoResearchProvider:
    """Deterministic demo research with sourced facts (development only)."""

    name = "demo_research"

    def status(self):
        from acquisition.integrations.discovery.types import ProviderStatus

        return ProviderStatus(configured=True, provider_name=self.name, message="Offline demo research — development only.")

    def fetch_public_context(self, *, company, website=None, domain=None, industry=None, icp=None, existing=None):
        from acquisition.integrations.discovery.types import (
            ContactPathInfo,
            DecisionMakerInfo,
            EvidenceItem,
            PublicResearchResult,
            PublicSignal,
        )

        existing = existing or {}
        rng = _rng("research|" + company)
        dom = domain or (company.split()[0].lower() + "health.com" if existing.get("npi") else None)
        site = website or (f"https://{dom}" if dom else None)
        emp = rng.choice([12, 24, 38, 55, 80, 140, 260])
        fn, ln = rng.choice(_OWNERS)
        dm_name = existing.get("executive_name") if existing.get("executive_name") not in (None, "", "Unknown decision-maker") else f"{fn} {ln}"
        dm_title = existing.get("executive_title") or rng.choice(_TITLES)
        team = f"{site}/team" if site else None
        has_email = rng.random() < 0.7
        low_name = company.lower()
        excluded = "law firm" if "law firm" in low_name else ("accounting firm" if "accounting" in low_name else None)
        signals = []
        if rng.random() < 0.6 and site:
            sig = rng.choice(["Hiring an operations manager", "Opened a second office", "Launched a new service line", "Named a new COO"])
            signals.append(PublicSignal(text=sig, category="hiring" if "Hiring" in sig else "expansion", confidence=0.6,
                                        evidence=[EvidenceItem(source_url=f"{site}/news", source_type="web_search", category="timing", note=f"{company}: {sig}", confidence=0.6)]))
        return PublicResearchResult(
            company_name=company,
            website=site,
            domain=dom,
            city=existing.get("city"),
            state=existing.get("state"),
            industry=industry,
            company_description=f"{company} is an established {industry or 'professional services'} firm with about {emp} people.",
            revenue_band=f"~${emp * 0.15:.0f}–{emp * 0.25:.0f}M (estimated from ~{emp} staff)",
            headcount_band=f"est. {emp}",
            decision_maker=DecisionMakerInfo(
                name=dm_name, title=dm_title, authority_type="founder_or_ceo", is_founder_or_equivalent=True, confidence=0.8,
                evidence=[EvidenceItem(source_url=team, source_type="web_search", category="decision_maker", note=f"{company}: {dm_name} — {dm_title}", confidence=0.8)],
            ) if team else None,
            contact=ContactPathInfo(
                email=(f"{dm_name.split()[0].lower()}@{dom}" if has_email and dom else None),
                email_confidence="unverified" if has_email else None,
                company_phone=existing.get("company_phone"),
                evidence=[EvidenceItem(source_url=f"{site}/contact", source_type="web_search", category="contact", note=f"{company}: published contact", confidence=0.6)] if site and has_email else [],
            ),
            signals=signals,
            research_notes="Demo research (offline).",
            evidence=[EvidenceItem(source_url=team, source_type="web_search", category="research", note=f"{company}: team page lists ~{emp} staff", confidence=0.6)] if team else [],
            company_identity_confidence=0.85,
            identity_uncertain=False,
            classification={"business_type": industry, "excluded_match": excluded, "employee_estimate": emp, "source": "demo"},
            cost_usd=round(rng.uniform(0.01, 0.05), 4),
        )
