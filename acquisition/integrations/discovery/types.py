"""Shared types for autonomous discovery / public research providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


CONFIDENCE_HIGH = 0.85
CONFIDENCE_MEDIUM = 0.55
CONFIDENCE_LOW = 0.25

CONFIDENCE_LABELS = {
    "high": CONFIDENCE_HIGH,
    "medium": CONFIDENCE_MEDIUM,
    "low": CONFIDENCE_LOW,
}


def confidence_value(label: str | float | None, default: float = CONFIDENCE_MEDIUM) -> float:
    if label is None:
        return default
    if isinstance(label, (int, float)):
        return float(label)
    return CONFIDENCE_LABELS.get(str(label).strip().lower(), default)


@dataclass(slots=True)
class DiscoveryCandidate:
    """Normalized company candidate from a discovery provider."""

    company: str
    website: str | None = None
    domain: str | None = None
    city: str | None = None
    state: str | None = None
    region: str | None = None
    industry: str | None = None
    source: str = "discovery"
    source_url: str | None = None
    source_type: str = "search"
    discovered_at: datetime | None = None
    notes: str | None = None
    identity_confidence: float | None = None
    identity_uncertain: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    # --- Discovery engine v2 (multi-source) ---
    discovery_source: str | None = None  # openai_web | legacy_search | mock | legacy labels
    slice_key: str | None = None
    npi: str | None = None
    description: str | None = None
    executive_name: str | None = None
    executive_title: str | None = None
    company_phone: str | None = None
    headcount_hint: int | None = None
    revenue_hint_usd: float | None = None
    postal_code: str | None = None


@dataclass(slots=True)
class EvidenceItem:
    source_url: str | None = None
    source_type: str = "other"
    category: str | None = None
    note: str | None = None
    confidence: float | None = None
    checked_at: datetime | None = None


@dataclass(slots=True)
class DecisionMakerInfo:
    name: str | None = None
    title: str | None = None
    authority_type: str | None = None
    is_founder_or_equivalent: bool | None = None
    linkedin_url: str | None = None
    confidence: float | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass(slots=True)
class ContactPathInfo:
    email: str | None = None
    email_confidence: str | None = None  # high|medium|low|inferred|unverified|placeholder
    company_phone: str | None = None
    other_contact_path: str | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass(slots=True)
class PublicSignal:
    """Timing/need hypothesis — never a proven internal problem statement."""

    text: str
    category: str = "timing"
    confidence: float | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)


@dataclass(slots=True)
class PublicResearchResult:
    company_name: str | None = None
    website: str | None = None
    domain: str | None = None
    city: str | None = None
    state: str | None = None
    region: str | None = None
    industry: str | None = None
    company_description: str | None = None
    revenue_band: str | None = None
    headcount_band: str | None = None
    locations: list[Any] | None = None
    decision_maker: DecisionMakerInfo | None = None
    contact: ContactPathInfo | None = None
    signals: list[PublicSignal] = field(default_factory=list)
    research_notes: str | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    provider_errors: list[str] = field(default_factory=list)
    partial: bool = False
    company_identity_confidence: float | None = None
    identity_uncertain: bool = False
    # Bounded raw search rows for AI validation (title/snippet/url only).
    candidate_sources: list[dict[str, Any]] = field(default_factory=list)
    # AI business classification (business_type, excluded_match, employee_estimate).
    classification: dict[str, Any] | None = None
    cost_usd: float = 0.0


@dataclass(slots=True)
class ProviderStatus:
    configured: bool
    provider_name: str
    message: str
