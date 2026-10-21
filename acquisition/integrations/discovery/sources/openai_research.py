"""Public research of one company via OpenAI web search (ResearchProvider).

One Responses call per company. The model reads the company's own site and
public pages and returns structured facts, each tied to a source URL. Nothing
is accepted without a URL; emails are only kept when seen published on a page
and are marked unverified. Downstream hard rules (company-bound evidence,
AI validation, scoring) still apply.
"""

from __future__ import annotations

from typing import Any

from acquisition.integrations.discovery.sources.base import REGION_BY_STATE, state_code
from acquisition.integrations.discovery.sources.openai_web import OpenAIWebClient
from acquisition.integrations.discovery.types import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    ContactPathInfo,
    DecisionMakerInfo,
    EvidenceItem,
    ProviderStatus,
    PublicResearchResult,
    PublicSignal,
    confidence_value,
)
from acquisition.services.domain_tools import is_non_company_domain, root_domain

_INSTRUCTIONS = """You research ONE US company for a B2B prospect record. Use web search.
Rules:
- Only state facts you saw on a web page in this session. Every fact needs its source_url.
- Prefer the company's own website (About, Team, Leadership, Contact pages).
- The decision_maker is the founder / owner / CEO / managing partner / president (highest practical authority).
- email: only an address you saw PUBLISHED on a page (never guess patterns). Otherwise null.
- employee_estimate: count from a team page, stated headcount, or clearly stated size. null if unknown.
- business_type: what the firm primarily IS (e.g. "marketing agency", "management consulting", "fractional CFO firm", "psychology group practice", "law firm", "accounting firm", "construction contractor").
- excluded_match: if the firm itself primarily IS one of the excluded types, name that type; otherwise null. A firm that merely SERVES an excluded industry is not excluded.
- If you cannot find the company's own website, set website to null.
Return ONLY JSON:
{"company_name":"","website":"","city":"","state":"","business_type":"","excluded_match":null,
 "description":"","employee_estimate":null,"locations":[],
 "decision_maker":{"name":null,"title":null,"source_url":null,"confidence":"low|medium|high"},
 "email":{"address":null,"source_url":null},"phone":{"number":null,"source_url":null},
 "signals":[{"text":"","category":"hiring|expansion|leadership|acquisition|offering|technology|other","source_url":""}],
 "facts_sources":[{"fact":"","source_url":""}]}"""


def _revenue_band_from_headcount(employees: int | None) -> str | None:
    """Coarse estimate only — professional services ≈ $150K–$250K revenue per employee."""
    if not employees or employees <= 0:
        return None
    low = employees * 0.15
    high = employees * 0.25
    return f"~${low:.0f}–{high:.0f}M (estimated from ~{employees} staff)"


def _headcount_band(employees: int | None) -> str | None:
    if not employees:
        return None
    for top in (10, 25, 50, 100, 250, 500, 1000):
        if employees <= top:
            return f"≤{top} (est. {employees})"
    return f"1000+ (est. {employees})"


class OpenAIWebResearchProvider:
    name = "openai_web_research"

    def __init__(self, client: OpenAIWebClient) -> None:
        self.client = client
        self.last_cost_usd = 0.0

    def status(self) -> ProviderStatus:
        if not self.client.configured:
            return ProviderStatus(
                configured=False,
                provider_name=self.name,
                message="Connect OpenAI under Integrations to enable web research.",
            )
        return ProviderStatus(
            configured=True,
            provider_name=self.name,
            message=f"OpenAI web research ready ({self.client.model}).",
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
        existing = existing or {}
        if not self.client.configured:
            return PublicResearchResult(
                company_name=company,
                website=website,
                domain=domain,
                industry=industry,
                partial=True,
                provider_errors=["openai_not_configured"],
                identity_uncertain=True,
                company_identity_confidence=CONFIDENCE_LOW,
            )
        excluded = ", ".join(str(x) for x in ((icp or {}).get("excluded_categories") or []))
        known = [
            f"company: {company}",
            f"website: {website or domain or 'unknown — find it'}",
            f"industry hint: {industry or 'unknown'}",
        ]
        if existing.get("city") or existing.get("state"):
            known.append(f"location: {existing.get('city') or ''} {existing.get('state') or ''}".strip())
        if existing.get("npi"):
            known.append(f"NPI registry number: {existing.get('npi')} (a registered healthcare organization)")
        if existing.get("executive_name") and existing.get("executive_name") != "Unknown decision-maker":
            known.append(
                f"registry-listed official: {existing.get('executive_name')}"
                f" ({existing.get('executive_title') or 'title unknown'})"
            )
        prompt = "\n".join(known) + f"\nexcluded_types: {excluded or 'none'}"
        answer = self.client.ask(instructions=_INSTRUCTIONS, prompt=prompt, context_size="medium")
        self.last_cost_usd = answer.cost_usd
        if answer.error or not answer.data:
            return PublicResearchResult(
                company_name=company,
                website=website,
                domain=domain,
                industry=industry,
                partial=True,
                provider_errors=[answer.error or "openai_research_unparseable"],
                identity_uncertain=True,
                company_identity_confidence=CONFIDENCE_LOW,
                candidate_sources=[
                    {"title": s.get("title"), "url": s.get("url"), "snippet": ""} for s in answer.sources
                ],
            )
        result = self._to_result(
            answer.data,
            answer.sources,
            company=company,
            website=website,
            domain=domain,
            industry=industry,
            existing=existing,
        )
        result.cost_usd = answer.cost_usd
        return result

    def _to_result(
        self,
        data: dict[str, Any],
        sources: list[dict[str, str]],
        *,
        company: str,
        website: str | None,
        domain: str | None,
        industry: str | None,
        existing: dict[str, Any],
    ) -> PublicResearchResult:
        found_site = str(data.get("website") or "").strip() or None
        found_domain = root_domain(found_site) if found_site else None
        if found_domain and is_non_company_domain(found_domain):
            found_site, found_domain = None, None
        final_domain = root_domain(domain) or found_domain
        final_site = website or (f"https://{found_domain}" if found_domain else None)

        evidence: list[EvidenceItem] = []
        for fs in data.get("facts_sources") or []:
            if isinstance(fs, dict) and fs.get("source_url"):
                evidence.append(
                    EvidenceItem(
                        source_url=str(fs["source_url"]),
                        source_type="web_search",
                        category="research",
                        note=f"{company}: {str(fs.get('fact') or '')[:300]}",
                        confidence=CONFIDENCE_MEDIUM,
                    )
                )

        dm_raw = data.get("decision_maker") or {}
        dm = None
        if isinstance(dm_raw, dict) and dm_raw.get("name") and dm_raw.get("source_url"):
            conf = confidence_value(dm_raw.get("confidence"), CONFIDENCE_MEDIUM)
            dm = DecisionMakerInfo(
                name=str(dm_raw["name"]).strip(),
                title=(str(dm_raw.get("title") or "").strip() or None),
                authority_type="founder_or_ceo",
                is_founder_or_equivalent=True,
                confidence=conf,
                evidence=[
                    EvidenceItem(
                        source_url=str(dm_raw["source_url"]),
                        source_type="web_search",
                        category="decision_maker",
                        note=f"{company}: {dm_raw['name']} — {dm_raw.get('title') or 'leader'}",
                        confidence=conf,
                    )
                ],
            )

        email_raw = data.get("email") or {}
        phone_raw = data.get("phone") or {}
        contact = ContactPathInfo()
        if isinstance(email_raw, dict) and email_raw.get("address") and email_raw.get("source_url"):
            addr = str(email_raw["address"]).strip()
            if "@" in addr:
                contact.email = addr
                contact.email_confidence = "unverified"
                contact.evidence.append(
                    EvidenceItem(
                        source_url=str(email_raw["source_url"]),
                        source_type="web_search",
                        category="contact",
                        note=f"{company}: published email {addr}",
                        confidence=CONFIDENCE_MEDIUM,
                    )
                )
        if isinstance(phone_raw, dict) and phone_raw.get("number"):
            contact.company_phone = str(phone_raw["number"]).strip()
        if not contact.company_phone and existing.get("company_phone"):
            contact.company_phone = existing.get("company_phone")

        signals: list[PublicSignal] = []
        for sig in data.get("signals") or []:
            if not isinstance(sig, dict) or not sig.get("text") or not sig.get("source_url"):
                continue
            signals.append(
                PublicSignal(
                    text=str(sig["text"])[:300],
                    category=str(sig.get("category") or "timing"),
                    confidence=CONFIDENCE_MEDIUM,
                    evidence=[
                        EvidenceItem(
                            source_url=str(sig["source_url"]),
                            source_type="web_search",
                            category=str(sig.get("category") or "timing"),
                            note=f"{company}: {str(sig['text'])[:200]}",
                            confidence=CONFIDENCE_MEDIUM,
                        )
                    ],
                )
            )

        emp = data.get("employee_estimate")
        try:
            employees = int(emp) if emp not in (None, "") else None
        except (TypeError, ValueError):
            employees = None
        state = state_code(data.get("state")) or state_code(existing.get("state"))
        city = str(data.get("city") or "").strip() or existing.get("city")
        has_site = bool(final_domain)
        # A federal registry record (NPI) is an authoritative identity even before
        # the website is found.
        registry_identity = bool(existing.get("npi"))
        identity_conf = CONFIDENCE_HIGH if (has_site or registry_identity) else CONFIDENCE_LOW
        classification = {
            "business_type": str(data.get("business_type") or "").strip() or None,
            "excluded_match": (str(data.get("excluded_match")).strip() if data.get("excluded_match") else None),
            "employee_estimate": employees,
            "source": "openai_web_research",
        }
        result = PublicResearchResult(
            company_name=str(data.get("company_name") or company).strip() or company,
            website=final_site,
            domain=final_domain,
            city=city,
            state=state,
            region=REGION_BY_STATE.get(state or ""),
            industry=classification["business_type"] or industry,
            company_description=str(data.get("description") or "").strip()[:1000] or None,
            revenue_band=_revenue_band_from_headcount(employees),
            headcount_band=_headcount_band(employees),
            locations=[x for x in (data.get("locations") or []) if isinstance(x, str)][:10] or None,
            decision_maker=dm,
            contact=contact,
            signals=signals,
            research_notes=(
                f"OpenAI web research. Business type: {classification['business_type'] or 'unknown'}."
                + (f" Excluded match: {classification['excluded_match']}." if classification["excluded_match"] else "")
            ),
            evidence=evidence,
            partial=not has_site,
            company_identity_confidence=identity_conf,
            identity_uncertain=not (has_site or registry_identity),
            candidate_sources=[
                {"title": s.get("title"), "url": s.get("url"), "snippet": ""} for s in sources[:12]
            ],
        )
        result.classification = classification
        return result
