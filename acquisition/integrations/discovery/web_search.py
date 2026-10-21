"""Live public web-search discovery/research provider (permitted search APIs only).

Supports configurable endpoints (Serper-compatible POST, Brave-compatible GET,
or generic JSON). No login walls, CAPTCHA bypass, LinkedIn/YouTube credentials,
or paid enrichment vendors.

Emails are never fabricated. If an address appears in public snippet text it is
stored as inferred/unverified only.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.base import DiscoveryProviderError
from acquisition.integrations.discovery.types import (
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
from acquisition.services.company_identity import (
    IDENTITY_UNCERTAIN_THRESHOLD,
    CompanyIdentity,
    domain_from_url as identity_domain_from_url,
    email_matches_company_domain,
    is_credible_leadership_title,
    is_generic_service_title,
    is_service_keyword_page_not_company_type,
    is_source_relevant_to_prospect,
    is_valid_person_name,
    prefer_canonical_company_name,
    resolve_company_identity,
)
from acquisition.services import acquisition_trace as acq_trace

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b"
)
_PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)
_TITLE_PATTERNS = (
    (re.compile(r"\b(founder(?:\s*[&/]\s*ceo)?|co[- ]?founder)\b", re.I), "founder", True),
    (re.compile(r"\b(owner|principal)\b", re.I), "owner", True),
    (re.compile(r"\b(chief executive officer|\bceo\b)\b", re.I), "ceo", True),
    (re.compile(r"\b(managing partner|managing director)\b", re.I), "managing partner", True),
    (re.compile(r"\b(president)\b", re.I), "president", True),
)
_DM_NAME_RE = re.compile(
    r"\b([A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){1,2})\s*,?\s+"
    r"(Founder|Co-Founder|Co Founder|CEO|Owner|President|Managing Partner|"
    r"Managing Director|Chief Executive Officer|Principal)\b",
)
_SIGNAL_RULES = (
    ("hiring", ("hiring", "careers", "job opening", "we're hiring", "open roles")),
    ("expansion", ("expansion", "new office", "new location", "opens clinic", "new clinic")),
    ("acquisition", ("acquires", "acquired", "acquisition")),
    ("leadership", ("appoints", "named ceo", "new leadership", "joins as")),
    ("funding", ("raises", "funding", "series a", "series b", "seed round")),
    ("offering", ("launches", "new service", "new offering", "announces")),
    ("technology", ("digital transformation", "ai ", "automation", "platform launch")),
)
_US_STATE_HINTS = {
    "tx": "TX",
    "texas": "TX",
    "ca": "CA",
    "california": "CA",
    "ny": "NY",
    "new york": "NY",
    "co": "CO",
    "colorado": "CO",
    "wa": "WA",
    "washington": "WA",
    "il": "IL",
    "illinois": "IL",
    "fl": "FL",
    "florida": "FL",
    "ga": "GA",
    "georgia": "GA",
    "ma": "MA",
    "massachusetts": "MA",
}
_BLOCKED_DOMAINS = {
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "google.com",
    "bing.com",
    "duckduckgo.com",
    "wikipedia.org",
    "crunchbase.com",
    "apollo.io",
    "zoominfo.com",
    "clay.com",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _domain_from_url(url: str | None) -> str | None:
    return identity_domain_from_url(url)


def _is_blocked_domain(domain: str | None) -> bool:
    if not domain:
        return True
    d = domain.lower()
    return any(d == b or d.endswith("." + b) for b in _BLOCKED_DOMAINS)


def _guess_state(blob: str) -> tuple[str | None, str | None]:
    lowered = blob.lower()
    for needle, code in _US_STATE_HINTS.items():
        if re.search(rf"\b{re.escape(needle)}\b", lowered):
            region = {
                "TX": "South",
                "FL": "South",
                "GA": "South",
                "CA": "West",
                "WA": "West",
                "CO": "West",
                "NY": "Northeast",
                "MA": "Northeast",
                "IL": "Midwest",
            }.get(code)
            return code, region
    return None, None


class WebSearchClient:
    """Thin HTTP client for configurable public search APIs."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self.last_error: str | None = None

    @property
    def api_key(self) -> str:
        return (self.settings.discovery_search_api_key or "").strip()

    @property
    def base_url(self) -> str:
        configured = (self.settings.discovery_search_base_url or "").strip()
        if configured:
            return configured.rstrip("/")
        provider = (self.settings.discovery_provider or "").strip().lower()
        if provider in {"brave", "brave_search"}:
            return "https://api.search.brave.com/res/v1/web/search"
        # Default: Serper-compatible Google search endpoint.
        return "https://google.serper.dev/search"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def search(self, query: str, *, num: int = 10) -> list[dict[str, Any]]:
        if not self.configured:
            raise DiscoveryProviderError(
                "discovery_provider_configuration_required",
                provider="web_search",
            )
        timeout = float(self.settings.discovery_timeout_seconds or 15)
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client:
                provider = (self.settings.discovery_provider or "").strip().lower()
                if provider in {"brave", "brave_search"} or "brave.com" in self.base_url:
                    resp = client.get(
                        self.base_url,
                        params={"q": query, "count": max(1, min(num, 20))},
                        headers={
                            "Accept": "application/json",
                            "X-Subscription-Token": self.api_key,
                            "Authorization": f"Bearer {self.api_key}",
                        },
                    )
                else:
                    # Serper-compatible + generic JSON POST.
                    resp = client.post(
                        self.base_url,
                        headers={
                            "X-API-KEY": self.api_key,
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={"q": query, "num": max(1, min(num, 20)), "gl": "us"},
                    )
                if resp.status_code >= 400:
                    self.last_error = f"http_{resp.status_code}"
                    raise DiscoveryProviderError(
                        f"search_http_{resp.status_code}",
                        provider="web_search",
                    )
                payload = resp.json()
        except DiscoveryProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = exc.__class__.__name__
            logger.warning("web search failed: %s", exc)
            raise DiscoveryProviderError(str(exc), provider="web_search") from exc

        return self._normalize_results(payload)

    @staticmethod
    def _normalize_results(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            raw_items = payload
        elif isinstance(payload, dict):
            raw_items = (
                payload.get("organic")
                or payload.get("organic_results")
                or payload.get("results")
                or (payload.get("web") or {}).get("results")
                or payload.get("items")
                or []
            )
        else:
            raw_items = []
        out: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            link = str(
                item.get("link")
                or item.get("url")
                or item.get("href")
                or ""
            ).strip()
            snippet = str(
                item.get("snippet")
                or item.get("description")
                or item.get("content")
                or ""
            ).strip()
            if not title and not link:
                continue
            out.append({"title": title, "link": link, "snippet": snippet})
        return out


class WebSearchDiscoveryProvider:
    """ICP-driven live company discovery via permitted public search."""

    name = "web_search_discovery"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: WebSearchClient | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or WebSearchClient(settings=self.settings, transport=transport)

    def status(self) -> ProviderStatus:
        if not self.client.configured:
            return ProviderStatus(
                configured=False,
                provider_name=self.name,
                message=(
                    "Live web-search discovery requires DISCOVERY_SEARCH_API_KEY "
                    "(and optional DISCOVERY_SEARCH_BASE_URL). configuration_required."
                ),
            )
        return ProviderStatus(
            configured=True,
            provider_name=self.name,
            message=f"Live public search ready ({self.client.base_url}).",
        )

    def search_companies(
        self,
        *,
        icp: dict[str, Any],
        limit: int = 5,
    ) -> list[DiscoveryCandidate]:
        status = self.status()
        if not status.configured:
            raise DiscoveryProviderError(
                "discovery_provider_configuration_required",
                provider=self.name,
            )
        queries = self._build_queries(icp)
        if acq_trace.is_acquisition_trace_enabled(self.settings):
            for i, query in enumerate(queries, start=1):
                acq_trace.trace_query(
                    index=i,
                    query=query,
                    reason="AI or deterministic ICP query plan",
                )
        seen_domains: set[str] = set()
        candidates: list[DiscoveryCandidate] = []
        errors: list[str] = []
        for query in queries:
            if len(candidates) >= limit:
                break
            try:
                results = self.client.search(query, num=max(limit * 2, 8))
            except DiscoveryProviderError as exc:
                errors.append(str(exc))
                continue
            acq_trace.trace_search(
                provider=self.name, query=query, raw_results=len(results)
            )
            for row in results:
                cand = self._candidate_from_result(row, icp=icp)
                if cand is None:
                    continue
                domain = (cand.domain or "").lower()
                if domain in seen_domains:
                    acq_trace.trace_candidate(
                        company=cand.company,
                        decision="REJECT",
                        reasons=["duplicate search result (domain already seen)"],
                    )
                    continue
                seen_domains.add(domain)
                candidates.append(cand)
                if len(candidates) >= limit:
                    break
        if not candidates and errors:
            raise DiscoveryProviderError(
                errors[0],
                provider=self.name,
            )
        return candidates[:limit]

    def search_company_context(
        self,
        *,
        company: str,
        domain: str | None = None,
        website: str | None = None,
        icp: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        del icp
        query = f'"{company}" United States company'
        if domain:
            query += f" site:{domain}"
        try:
            results = self.client.search(query, num=5)
        except DiscoveryProviderError:
            return []
        out: list[DiscoveryCandidate] = []
        for row in results:
            cand = self._candidate_from_result(row, icp={})
            if cand is None:
                continue
            if website and not cand.website:
                cand.website = website
            if domain and not cand.domain:
                cand.domain = domain
            out.append(cand)
        return out

    def _build_queries(self, icp: dict[str, Any]) -> list[str]:
        from acquisition.services.discovery_query_planning_service import (
            build_deterministic_discovery_queries,
            resolve_discovery_queries,
        )

        try:
            plan = resolve_discovery_queries(icp, settings=self.settings)
            if plan.queries:
                return plan.queries
        except Exception:  # noqa: BLE001
            logger.exception("discovery query planning failed; using deterministic fallback")
        return build_deterministic_discovery_queries(icp)

    def _candidate_from_result(
        self, row: dict[str, Any], *, icp: dict[str, Any]
    ) -> DiscoveryCandidate | None:
        link = str(row.get("link") or "").strip()
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        domain = _domain_from_url(link)
        if _is_blocked_domain(domain):
            acq_trace.trace_candidate(
                company=title or domain or "unknown",
                decision="REJECT",
                reasons=["not a plausible company (blocked/social/directory domain)"],
            )
            return None
        identity = resolve_company_identity(
            title=title,
            url=link,
            snippet=snippet,
            domain_hint=domain,
            website_hint=f"https://{domain}" if domain else None,
        )
        company = identity.company_name
        excluded = [str(x).lower() for x in (icp.get("excluded_categories") or [])]
        blob = f"{title} {snippet} {company}".lower()
        hit_excluded = next((ex for ex in excluded if ex and ex in blob), None)
        if hit_excluded:
            acq_trace.trace_candidate(
                company=company or title or "unknown",
                decision="REJECT",
                reasons=[f"excluded category: {hit_excluded}"],
            )
            return None
        if is_service_keyword_page_not_company_type(
            title=title,
            snippet=snippet,
            company=company,
            priority_categories=icp.get("priority_categories") or [],
            identity_uncertain=bool(identity.uncertain),
        ):
            acq_trace.trace_candidate(
                company=company or title or "unknown",
                decision="REJECT",
                reasons=[
                    "service offering page matching ICP keywords, "
                    "not evidence the company is that business type"
                ],
            )
            return None
        state, region = _guess_state(blob)
        industry = None
        for cat in (icp.get("priority_categories") or []):
            cat_l = str(cat).lower()
            # Prefer company-name evidence over title-only service keyword hits.
            if cat_l and company and cat_l in str(company).lower():
                industry = str(cat)
                break
            if cat_l and cat_l in blob and not is_generic_service_title(title):
                industry = str(cat)
                break
        website = identity.website or (f"https://{domain}" if domain else link or None)
        notes = "Live public search candidate (US-focused query)."
        if identity.uncertain or is_generic_service_title(title):
            notes += (
                " Company identity provisional — generic service-page title resolved "
                f"via {identity.resolution_notes or 'domain'}."
            )
        reasons = ["US geography matched (US-focused search)"]
        if industry:
            reasons.append(f"priority category matched: {industry}")
        else:
            reasons.append("plausible ICP fit (not excluded)")
        if not company or not str(company).strip():
            reasons.append("company identity unclear")
        elif identity.uncertain:
            reasons.append("identity provisional — needs research")
        acq_trace.trace_search_result(
            company=company,
            domain=identity.domain or domain,
            url=link or None,
            title=title,
            snippet=snippet,
        )
        acq_trace.trace_candidate(
            company=company or title or "unknown",
            decision="ACCEPT",
            reasons=reasons,
        )
        return DiscoveryCandidate(
            company=company,
            website=website,
            domain=identity.domain or domain,
            city=None,
            state=state,
            region=region,
            industry=industry,
            source="autonomous_discovery",
            source_url=link or None,
            source_type="search",
            discovered_at=_utcnow(),
            notes=notes,
            identity_confidence=identity.confidence,
            identity_uncertain=identity.uncertain,
            raw={
                "title": title,
                "snippet": snippet,
                "identity_resolution": identity.resolution_notes,
                "identity_confidence": identity.confidence,
            },
        )


class WebSearchResearchProvider:
    """Public research via permitted search snippets — no invented facts."""

    name = "web_search_research"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: WebSearchClient | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or WebSearchClient(settings=self.settings, transport=transport)

    def status(self) -> ProviderStatus:
        if not self.client.configured:
            return ProviderStatus(
                configured=False,
                provider_name=self.name,
                message="Live research requires DISCOVERY_SEARCH_API_KEY. configuration_required.",
            )
        return ProviderStatus(
            configured=True,
            provider_name=self.name,
            message="Live public research ready.",
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
        del icp
        if not self.status().configured:
            return PublicResearchResult(
                company_name=company,
                website=website,
                domain=domain,
                industry=industry,
                partial=True,
                provider_errors=["discovery_provider_configuration_required"],
                identity_uncertain=True,
                company_identity_confidence=CONFIDENCE_LOW,
            )

        domain = domain or _domain_from_url(website)
        identity = resolve_company_identity(
            title=company,
            company_hint=company,
            domain_hint=domain,
            website_hint=website,
            snippet=None,
            url=website,
        )
        # Prefer stronger brand when the stored company looks generic.
        canonical = prefer_canonical_company_name(
            current=company, candidate=identity.company_name
        ) or company
        if is_generic_service_title(canonical) and domain:
            # Keep domain-provisional name rather than a service-page label.
            canonical = identity.company_name or canonical

        research_queries = self._build_research_queries(
            company=canonical, domain=domain
        )
        if acq_trace.is_acquisition_trace_enabled(self.settings):
            for i, q in enumerate(research_queries, start=1):
                acq_trace.trace_query(
                    index=i,
                    query=q,
                    reason="public research after company identity established",
                )

        organic: list[dict[str, Any]] = []
        errors: list[str] = []
        for query in research_queries:
            try:
                rows = self.client.search(query, num=5)
                organic.extend(rows)
                acq_trace.trace_search(
                    provider=self.name, query=query, raw_results=len(rows)
                )
            except DiscoveryProviderError as exc:
                errors.append(str(exc))

        if not organic and errors:
            return PublicResearchResult(
                company_name=canonical,
                website=website,
                domain=domain,
                industry=industry,
                partial=True,
                provider_errors=errors[:3],
                identity_uncertain=True,
                company_identity_confidence=identity.confidence,
            )

        # Upgrade company name from same-domain / explicitly named results.
        upgraded = self._upgrade_company_name(
            current=canonical, domain=domain, organic=organic
        )
        if upgraded:
            canonical = upgraded
            identity = CompanyIdentity(
                company_name=canonical,
                domain=domain,
                website=website or (f"https://{domain}" if domain else None),
                confidence=max(identity.confidence, CONFIDENCE_MEDIUM),
                uncertain=False,
                source_url=website,
                resolution_notes="research_upgrade",
            )

        relevant_rows = [
            row
            for row in organic
            if is_source_relevant_to_prospect(
                source_url=str(row.get("link") or "") or None,
                company_name=canonical,
                domain=domain,
                source_title=str(row.get("title") or "") or None,
                source_snippet=str(row.get("snippet") or "") or None,
            )
        ]

        evidence: list[EvidenceItem] = []
        signals: list[PublicSignal] = []
        blob_parts: list[str] = []
        for row in relevant_rows:
            title = str(row.get("title") or "")
            snippet = str(row.get("snippet") or "")
            link = str(row.get("link") or "")
            blob_parts.append(f"{title} {snippet}")
            evidence.append(
                EvidenceItem(
                    source_url=link or None,
                    source_type="search",
                    category="research",
                    note=(snippet or title)[:240] or None,
                    confidence=CONFIDENCE_MEDIUM,
                    checked_at=_utcnow(),
                )
            )
            for category, needles in _SIGNAL_RULES:
                text_l = f"{title} {snippet}".lower()
                if any(n in text_l for n in needles):
                    hyp = f"Public signal suggests possible {category} activity."
                    signals.append(
                        PublicSignal(
                            text=hyp,
                            category="timing",
                            confidence=CONFIDENCE_LOW,
                            evidence=[
                                EvidenceItem(
                                    source_url=link or None,
                                    source_type="search",
                                    category=category,
                                    note=snippet[:200] or title[:200],
                                    confidence=CONFIDENCE_LOW,
                                    checked_at=_utcnow(),
                                )
                            ],
                        )
                    )

        blob = " ".join(blob_parts)
        state, region = _guess_state(blob)
        dm = self._extract_decision_maker(blob, relevant_rows, company=canonical, domain=domain)
        contact = self._extract_contact(
            blob,
            relevant_rows,
            domain=domain,
            company=canonical,
            existing=existing or {},
        )

        seen_sig: set[str] = set()
        unique_signals: list[PublicSignal] = []
        for sig in signals:
            if sig.text in seen_sig:
                continue
            seen_sig.add(sig.text)
            unique_signals.append(sig)

        description = None
        for row in relevant_rows[:3]:
            snip = str(row.get("snippet") or "").strip()
            if len(snip) > 40:
                description = snip[:500]
                break

        identity_uncertain = bool(
            identity.uncertain
            or identity.confidence < IDENTITY_UNCERTAIN_THRESHOLD
            or is_generic_service_title(canonical)
            or not domain
        )
        # Strong same-domain evidence clears provisional domain-only identity.
        if (
            identity_uncertain
            and domain
            and relevant_rows
            and not is_generic_service_title(canonical)
            and identity.resolution_notes == "research_upgrade"
        ):
            identity_uncertain = False

        return PublicResearchResult(
            company_name=canonical,
            website=website or (f"https://{domain}" if domain else None),
            domain=domain,
            city=None,
            state=state,
            region=region,
            industry=industry,
            company_description=description,
            revenue_band=None,  # never invent
            headcount_band=None,
            locations=None,
            decision_maker=dm,
            contact=contact,
            signals=unique_signals[:8],
            research_notes=(
                "Public web-search research (company-bound snippets only). "
                "Missing fields left unset — not invented."
                + (
                    " Identity uncertain — needs more research."
                    if identity_uncertain
                    else ""
                )
            ),
            evidence=evidence[:20],
            provider_errors=errors[:3],
            partial=True,
            company_identity_confidence=identity.confidence,
            identity_uncertain=identity_uncertain,
            candidate_sources=[
                {
                    "title": str(row.get("title") or ""),
                    "snippet": str(row.get("snippet") or ""),
                    "url": str(row.get("link") or "") or None,
                    "source_type": "search",
                }
                for row in organic[:12]
                if is_source_relevant_to_prospect(
                    source_url=str(row.get("link") or "") or None,
                    company_name=canonical,
                    domain=domain,
                    source_title=str(row.get("title") or "") or None,
                    source_snippet=str(row.get("snippet") or "") or None,
                )
                or (
                    domain
                    and _domain_from_url(str(row.get("link") or ""))
                    and (
                        _domain_from_url(str(row.get("link") or "")) == domain
                        or str(_domain_from_url(str(row.get("link") or "")) or "").endswith(
                            "." + domain
                        )
                    )
                )
            ],
        )

    @staticmethod
    def _build_research_queries(*, company: str, domain: str | None) -> list[str]:
        """Research queries AFTER company identity is established (not discovery queries)."""
        queries = [
            f'"{company}" United States about OR company',
            f'"{company}" (founder OR CEO OR owner OR "managing partner" OR president)',
            f'"{company}" (hiring OR careers OR jobs OR expansion OR "new office")',
            f'"{company}" (contact OR email OR phone)',
        ]
        if domain:
            queries.extend(
                [
                    f'site:{domain} about OR team OR contact',
                    f'site:{domain} careers OR hiring',
                    f'"{company}" OR site:{domain} leadership OR CEO',
                ]
            )
        # De-dupe
        seen: set[str] = set()
        out: list[str] = []
        for q in queries:
            if q in seen:
                continue
            seen.add(q)
            out.append(q)
        return out

    @staticmethod
    def _upgrade_company_name(
        *,
        current: str,
        domain: str | None,
        organic: list[dict[str, Any]],
    ) -> str | None:
        best = current
        for row in organic:
            link = str(row.get("link") or "")
            title = str(row.get("title") or "")
            snippet = str(row.get("snippet") or "")
            row_domain = _domain_from_url(link)
            if domain and row_domain and row_domain != domain and not row_domain.endswith(
                "." + domain
            ):
                # Only upgrade from same-domain pages or explicit brand titles.
                identity = resolve_company_identity(
                    title=title, url=link, snippet=snippet, domain_hint=domain
                )
                if not is_source_relevant_to_prospect(
                    source_url=link,
                    company_name=identity.company_name,
                    domain=domain,
                    source_title=title,
                    source_snippet=snippet,
                ):
                    continue
            identity = resolve_company_identity(
                title=title,
                url=link,
                snippet=snippet,
                company_hint=best,
                domain_hint=domain,
            )
            if is_generic_service_title(identity.company_name):
                continue
            preferred = prefer_canonical_company_name(
                current=best, candidate=identity.company_name
            )
            if preferred and preferred != best and not is_generic_service_title(preferred):
                # Prefer names discovered on the company domain.
                if domain and row_domain and (
                    row_domain == domain or row_domain.endswith("." + domain)
                ):
                    best = preferred
                elif not is_generic_service_title(best) and len(preferred) > len(best):
                    best = preferred
                elif is_generic_service_title(best) or len(best) <= 4:
                    best = preferred
        if best != current and not is_generic_service_title(best):
            return best
        return None

    def _extract_decision_maker(
        self,
        blob: str,
        organic: list[dict[str, Any]],
        *,
        company: str,
        domain: str | None,
    ) -> DecisionMakerInfo | None:
        for match in _DM_NAME_RE.finditer(blob):
            name = match.group(1).strip()
            title = match.group(2).strip()
            if not is_valid_person_name(name):
                continue
            if not is_credible_leadership_title(title):
                continue
            # Ensure the match came from a company-relevant source when possible.
            source_url = None
            source_ok = False
            for row in organic:
                text = f"{row.get('title')} {row.get('snippet')}"
                if name.split()[0] not in text:
                    continue
                link = str(row.get("link") or "") or None
                if is_source_relevant_to_prospect(
                    source_url=link,
                    company_name=company,
                    domain=domain,
                    source_title=str(row.get("title") or "") or None,
                    source_snippet=str(row.get("snippet") or "") or None,
                ):
                    source_url = link
                    source_ok = True
                    break
            if organic and not source_ok:
                continue
            authority = "founder"
            is_founder = True
            for pattern, auth, founder_flag in _TITLE_PATTERNS:
                if pattern.search(title):
                    authority = auth
                    is_founder = founder_flag
                    break
            return DecisionMakerInfo(
                name=name,
                title=title.title() if title.islower() else title,
                authority_type=authority,
                is_founder_or_equivalent=is_founder,
                linkedin_url=None,
                confidence=CONFIDENCE_LOW,
                evidence=[
                    EvidenceItem(
                        source_url=source_url,
                        source_type="search",
                        category="decision_maker",
                        note=f"Public snippet mentions {name} as {title}.",
                        confidence=CONFIDENCE_LOW,
                        checked_at=_utcnow(),
                    )
                ],
            )
        # No valid person identified — leave decision-maker unset (do not invent).
        return None

    def _extract_contact(
        self,
        blob: str,
        organic: list[dict[str, Any]],
        *,
        domain: str | None,
        company: str,
        existing: dict[str, Any],
    ) -> ContactPathInfo:
        emails = [m.group(1) for m in _EMAIL_RE.finditer(blob)]
        chosen = None
        for email in emails:
            if email_matches_company_domain(email, domain):
                chosen = email
                break
            # Reject emails from unrelated domains entirely.

        phone = None
        # Phone only from company-relevant rows (not mixed blob from rejected sources —
        # blob is already filtered, but double-check row attribution for contact pages).
        for row in organic:
            text = f"{row.get('title')} {row.get('snippet')}"
            if not is_source_relevant_to_prospect(
                source_url=str(row.get("link") or "") or None,
                company_name=company,
                domain=domain,
                source_title=str(row.get("title") or "") or None,
                source_snippet=str(row.get("snippet") or "") or None,
            ):
                continue
            phone_match = _PHONE_RE.search(text)
            if phone_match:
                phone = phone_match.group(0)
                break

        other = None
        for row in organic:
            link = str(row.get("link") or "")
            if not is_source_relevant_to_prospect(
                source_url=link or None,
                company_name=company,
                domain=domain,
                source_title=str(row.get("title") or "") or None,
                source_snippet=str(row.get("snippet") or "") or None,
            ):
                continue
            if "/contact" in link.lower() or "contact" in str(row.get("title") or "").lower():
                other = link
                break

        evidence: list[EvidenceItem] = []
        if chosen:
            evidence.append(
                EvidenceItem(
                    source_type="search",
                    category="contact",
                    note=f"Email address observed in public snippet text ({chosen}).",
                    confidence=CONFIDENCE_LOW,
                    checked_at=_utcnow(),
                )
            )
        del existing  # existing contact overwrite guarded in PublicResearchService
        return ContactPathInfo(
            email=chosen,
            email_confidence="inferred" if chosen else None,
            company_phone=phone,
            other_contact_path=other,
            evidence=evidence,
        )
