"""Public research / enrichment for autonomous acquisition (no Instantly activation)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.base import DiscoveryProviderError, ResearchProvider
from acquisition.integrations.discovery.factory import resolve_research_provider
from acquisition.integrations.discovery.types import (
    ContactPathInfo,
    DecisionMakerInfo,
    EvidenceItem,
    PublicResearchResult,
    PublicSignal,
)
from acquisition.models.prospect import AcquisitionStatus, Prospect
from acquisition.services.ai_research_validation_service import (
    RELEVANCE_EXTERNAL_NEWS,
    RELEVANCE_EXTERNAL_VERIFIED,
    RELEVANCE_IRRELEVANT,
    RELEVANCE_SAME_COMPANY,
    AIResearchValidationResult,
    AIResearchValidationService,
    is_headquarters_location,
)
from acquisition.services.autonomous_discovery_service import (
    UNKNOWN_DECISION_MAKER,
)
from acquisition.services.company_identity import (
    email_matches_company_domain,
    filter_sources_for_ai_validation,
    is_credible_leadership_title,
    is_generic_service_title,
    is_source_relevant_to_prospect,
    is_valid_person_name,
    prefer_canonical_company_name,
)
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.prospect_identity import (
    domain_from_website_or_email,
    is_placeholder_email,
    normalize_domain,
    placeholder_email_for_domain,
)
from acquisition.services.prospect_research_service import ProspectResearchService
from acquisition.services.prospect_scoring_service import ProspectScoringService
from acquisition.services.prospect_service import ProspectNotFoundError
from acquisition.services import acquisition_trace as acq_trace

logger = logging.getLogger(__name__)


class PublicResearchService:
    """Research a Prospect from permitted public sources, persist, then score."""

    def __init__(
        self,
        db: Session,
        *,
        provider: ResearchProvider | None = None,
        settings: Settings | None = None,
        ai_validator: AIResearchValidationService | None = None,
        openai_call: Callable[[list[dict[str, str]], str], str] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.provider = provider or resolve_research_provider(self.settings, db=db)
        self.icp = IcpSettingsService(db)
        self.evidence = ProspectResearchService(db)
        self.scoring = ProspectScoringService(db)
        self.ai_validator = ai_validator or AIResearchValidationService(
            db, settings=self.settings, openai_call=openai_call
        )

    def research_prospect(self, prospect_id: uuid.UUID) -> dict[str, Any]:
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise ProspectNotFoundError(prospect_id)

        snapshot = {
            "company": prospect.company,
            "email": prospect.email,
            "executive_name": prospect.executive_name,
            "domain": prospect.domain,
            "website": prospect.website,
        }
        errors: list[str] = []
        result: PublicResearchResult | None = None
        try:
            result = self._fetch_with_retries(prospect)
        except DiscoveryProviderError as exc:
            errors.append(str(exc))
            logger.warning("research provider error for %s: %s", prospect_id, exc)
            return {
                "ok": False,
                "prospect_id": str(prospect_id),
                "errors": errors,
                "corrupted": False,
                "snapshot": snapshot,
                "scoring": None,
                "identity_uncertain": True,
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"unexpected_research_error: {exc}")
            logger.exception("unexpected research failure for %s", prospect_id)
            return {
                "ok": False,
                "prospect_id": str(prospect_id),
                "errors": errors,
                "corrupted": False,
                "snapshot": snapshot,
                "scoring": None,
                "identity_uncertain": True,
            }

        assert result is not None
        errors.extend(result.provider_errors)

        merged = self._merge_if_domain_taken(prospect, result)
        if merged is not None:
            return merged

        ai = self._run_ai_validation(prospect, result)
        if ai.error:
            errors.append(f"ai_validation:{ai.error}")
        result = self._merge_ai_validation(result, ai, prospect=prospect)

        # Clean polluted historical evidence against the resolved identity first.
        resolved_company = result.company_name or prospect.company
        resolved_domain = normalize_domain(result.domain) or normalize_domain(prospect.domain)
        cleanup = self._cleanup_polluted_evidence(
            prospect, company=resolved_company, domain=resolved_domain
        )

        identity_uncertain = self._apply_research(prospect, result)
        if identity_uncertain:
            prospect.acquisition_status = AcquisitionStatus.NEEDS_MORE_RESEARCH
        else:
            prospect.acquisition_status = AcquisitionStatus.RESEARCHED
        prospect.last_researched_at = datetime.now(timezone.utc)
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)

        dm = result.decision_maker
        contact = result.contact
        contact_path = None
        if contact:
            if contact.email and not is_placeholder_email(contact.email):
                contact_path = f"email:{contact.email_confidence or 'unverified'}"
            elif contact.company_phone:
                contact_path = "phone"
            elif contact.other_contact_path:
                contact_path = "other"
        source_urls = [
            e.source_url
            for e in (result.evidence or [])
            if getattr(e, "source_url", None)
        ]
        conf = None
        if result.company_identity_confidence is not None:
            c = float(result.company_identity_confidence)
            conf = "high" if c >= 0.75 else "medium" if c >= 0.4 else "low"
        acq_trace.trace_research(
            company=result.company_name or prospect.company,
            decision_maker=(dm.name if dm else None) or prospect.executive_name,
            title=(dm.title if dm else None) or prospect.executive_title,
            revenue=result.revenue_band or prospect.revenue_band,
            headcount=result.headcount_band or prospect.headcount_band,
            city=result.city or prospect.city,
            state=result.state or prospect.state,
            contact_path=contact_path,
            timing_signals=[s.text for s in (result.signals or [])][:8],
            sources=len(result.evidence or []) + len(result.candidate_sources or []),
            confidence=conf,
            source_urls=[u for u in source_urls if u][:8],
        )
        if ai.available or ai.error:
            acq_trace.trace_ai(
                task="prospect_validation",
                model=ai.model or getattr(self.settings, "research_validation_model", None),
                input_summary=(
                    f"{result.company_name or prospect.company}, "
                    f"domain={result.domain or prospect.domain or 'unknown'}, "
                    f"sources_sent={ai.sources_sent}, "
                    f"hard_rejected={ai.sources_hard_rejected}"
                ),
                result={
                    "same_company": ai.same_company,
                    "company_name": ai.company_name,
                    "decision_maker": ai.decision_maker_name,
                    "title": ai.decision_maker_title,
                    "city": ai.city,
                    "state": ai.state,
                    "industry": ai.industry,
                    "reason": ai.reason,
                    "error": ai.error,
                    "available": ai.available,
                },
            )

        return {
            "ok": True,
            "prospect_id": str(prospect_id),
            "errors": errors,
            "partial": result.partial,
            "signals": [s.text for s in result.signals],
            "scoring": None,
            "identity_uncertain": identity_uncertain,
            "company_identity_confidence": result.company_identity_confidence,
            "ai_validation_available": ai.available,
            "ai_validation_reason": ai.reason,
            "cleanup": cleanup,
            "cost_usd": float(getattr(result, "cost_usd", 0.0) or 0.0),
            "business_type": prospect.business_type,
        }

    def research_and_score_prospect(self, prospect_id: uuid.UUID) -> dict[str, Any]:
        research = self.research_prospect(prospect_id)
        if not research.get("ok") or research.get("merged") or research.get("duplicate_of"):
            return research
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise ProspectNotFoundError(prospect_id)

        identity_uncertain = bool(research.get("identity_uncertain"))
        if identity_uncertain:
            scoring = self.scoring.score_prospect(prospect, persist=True)
            prospect.acquisition_status = AcquisitionStatus.NEEDS_MORE_RESEARCH
            self.db.add(prospect)
            self.db.commit()
            self.db.refresh(prospect)
            research["scoring"] = scoring
            research["acquisition_status"] = AcquisitionStatus.NEEDS_MORE_RESEARCH.value
            return research

        prospect.acquisition_status = AcquisitionStatus.READY_FOR_SCORING
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)
        scoring = self.scoring.score_prospect(prospect, persist=True)
        research["scoring"] = scoring
        research["acquisition_status"] = (
            prospect.acquisition_status.value if prospect.acquisition_status else None
        )
        return research

    def _merge_if_domain_taken(
        self, prospect: Prospect, result: PublicResearchResult
    ) -> dict[str, Any] | None:
        """Research found a website that already belongs to another discovered record.

        Typical case: an NPPES registry practice (no website at discovery) turns out
        to be a company another source already found. Merge the registry facts
        into the existing record instead of creating a duplicate company.
        """
        from sqlalchemy import select as _select

        from acquisition.models.campaign import CampaignProspect
        from acquisition.services import live_trace

        found = normalize_domain(result.domain) or domain_from_website_or_email(
            website=result.website, email=None
        )
        if not found or normalize_domain(prospect.domain) == found:
            return None
        keeper = self.db.scalar(
            _select(Prospect)
            .where(Prospect.domain == found, Prospect.id != prospect.id, Prospect.discovery_source.is_not(None))
            .limit(1)
        )
        if keeper is None:
            return None
        has_links = bool(
            self.db.scalar(_select(CampaignProspect.id).where(CampaignProspect.prospect_id == prospect.id).limit(1))
        )
        if has_links:
            prospect.classification_json = {
                **(prospect.classification_json or {}),
                "duplicate_of": str(keeper.id),
            }
            prospect.acquisition_status = AcquisitionStatus.NEEDS_MORE_RESEARCH
            self.db.add(prospect)
            self.db.commit()
            live_trace.event(
                "dedupe",
                f"{prospect.company}: website {found} belongs to {keeper.company} — flagged as possible duplicate",
                level="warning",
                company=prospect.company,
                prospect_id=prospect.id,
            )
            return {
                "ok": True,
                "prospect_id": str(prospect.id),
                "errors": [],
                "acquisition_status": AcquisitionStatus.NEEDS_MORE_RESEARCH.value,
                "duplicate_of": str(keeper.id),
            }

        unknown = UNKNOWN_DECISION_MAKER.lower()
        if prospect.npi and not keeper.npi:
            npi = prospect.npi
            prospect.npi = None
            self.db.flush()
            keeper.npi = npi
        if (keeper.executive_name or "").strip().lower() in {"", unknown} and (
            (prospect.executive_name or "").strip().lower() not in {"", unknown}
        ):
            keeper.executive_name = prospect.executive_name
            keeper.executive_title = prospect.executive_title
            keeper.is_founder_or_equivalent = prospect.is_founder_or_equivalent
            keeper.authority_type = prospect.authority_type
        keeper.company_phone = keeper.company_phone or prospect.company_phone
        keeper.city = keeper.city or prospect.city
        keeper.state = keeper.state or prospect.state
        keeper.times_seen = int(keeper.times_seen or 0) + int(prospect.times_seen or 0)
        keeper.last_seen_at = prospect.last_seen_at or keeper.last_seen_at
        for src in list(prospect.research_sources or []):
            src.prospect_id = keeper.id
            self.db.add(src)
        self.db.flush()
        dup_name, dup_id = prospect.company, prospect.id
        self.db.delete(prospect)
        self.db.add(keeper)
        self.db.commit()
        self.db.refresh(keeper)
        live_trace.event(
            "dedupe",
            f"Merged {dup_name} into existing record {keeper.company} (same website {found})",
            level="warning",
            company=keeper.company,
            prospect_id=keeper.id,
        )
        return {
            "ok": True,
            "prospect_id": str(keeper.id),
            "merged_from": str(dup_id),
            "errors": [],
            "acquisition_status": keeper.acquisition_status.value if keeper.acquisition_status else None,
            "merged": True,
        }

    def _fetch_with_retries(self, prospect: Prospect) -> PublicResearchResult:
        retries = max(0, int(self.settings.discovery_max_retries or 1))
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self.provider.fetch_public_context(
                    company=prospect.company,
                    website=prospect.website,
                    domain=prospect.domain,
                    industry=prospect.industry,
                    icp=self.icp.as_dict(),
                    existing={
                        "executive_name": prospect.executive_name,
                        "executive_title": prospect.executive_title,
                        "email": prospect.email,
                        "industry": prospect.industry,
                        "npi": prospect.npi,
                        "city": prospect.city,
                        "state": prospect.state,
                        "company_phone": prospect.company_phone,
                    },
                )
            except DiscoveryProviderError as exc:
                last_error = exc
                logger.warning(
                    "research attempt %s failed for %s: %s",
                    attempt + 1,
                    prospect.id,
                    exc,
                )
        assert last_error is not None
        raise last_error

    def _run_ai_validation(
        self, prospect: Prospect, result: PublicResearchResult
    ) -> AIResearchValidationResult:
        sources: list[dict[str, Any]] = []
        # Prefer provider-supplied candidate search rows (pre-persist batch).
        for row in result.candidate_sources or []:
            if isinstance(row, dict):
                sources.append(row)
        for item in result.evidence or []:
            sources.append(
                {
                    "title": item.note or item.category or "",
                    "snippet": item.note or "",
                    "url": item.source_url,
                    "source_type": item.source_type,
                }
            )
        for signal in result.signals or []:
            for item in signal.evidence or []:
                sources.append(
                    {
                        "title": signal.text,
                        "snippet": item.note or signal.text,
                        "url": item.source_url,
                        "source_type": item.source_type or "signal",
                    }
                )
        if result.company_description:
            sources.append(
                {
                    "title": "company_description",
                    "snippet": result.company_description,
                    "url": result.website,
                    "source_type": "description",
                }
            )

        company = result.company_name or prospect.company
        domain = result.domain or prospect.domain
        # Hard filter before AI — unrelated domains never reach the model.
        grounded = filter_sources_for_ai_validation(
            sources, company_name=company, domain=domain
        )
        return self.ai_validator.validate_batch(
            company=company,
            domain=domain,
            website=result.website or prospect.website,
            source_url=prospect.website,
            industry=result.industry or prospect.industry,
            sources=grounded,
            existing={
                "executive_name": prospect.executive_name,
                "email": prospect.email,
                "company": prospect.company,
            },
        )

    def _merge_ai_validation(
        self,
        result: PublicResearchResult,
        ai: AIResearchValidationResult,
        *,
        prospect: Prospect,
    ) -> PublicResearchResult:
        """Intersect AI output with hard safety rules. AI never overrides hard rejects."""
        min_rel = float(getattr(self.settings, "ai_research_min_relevance_confidence", 0.70) or 0.70)
        min_fact = float(getattr(self.settings, "ai_research_min_fact_confidence", 0.70) or 0.70)
        min_dm = float(getattr(self.settings, "ai_research_min_dm_confidence", 0.80) or 0.80)

        domain = normalize_domain(result.domain) or normalize_domain(prospect.domain)
        company = result.company_name or prospect.company

        if not ai.available:
            note = (result.research_notes or "").strip()
            fallback = "AI validation unavailable — deterministic company-bound rules only."
            result.research_notes = f"{note}\n{fallback}".strip() if note else fallback
            # Still drop deterministic DMs that lack grounded support.
            result.decision_maker = self._ground_decision_maker(
                result.decision_maker, company=company, domain=domain
            )
            return result

        def _hard_url_ok(url: str | None, *, title: str | None = None, snippet: str | None = None) -> bool:
            if not url:
                return False
            return is_source_relevant_to_prospect(
                source_url=url,
                company_name=company,
                domain=domain,
                source_title=title,
                source_snippet=snippet,
            )

        # Company identity upgrade — requires supporting grounded source.
        if (
            ai.company_name
            and ai.company_name_confidence >= min_fact
            and not is_generic_service_title(ai.company_name)
            and _hard_url_ok(ai.company_name_source_url)
        ):
            preferred = prefer_canonical_company_name(
                current=company, candidate=ai.company_name
            )
            if preferred and not is_generic_service_title(preferred):
                company = preferred
                result.company_name = preferred
                result.company_identity_confidence = max(
                    result.company_identity_confidence or 0.0,
                    ai.company_name_confidence,
                )
                result.identity_uncertain = False

        # Source relevance: hard rejection wins; AI cannot revive unrelated URLs.
        allowed_urls: set[str] = set()
        for verdict in ai.sources or []:
            url = (verdict.source_url or "").strip()
            if not url:
                continue
            if verdict.relevance in {RELEVANCE_IRRELEVANT, "irrelevant", "unrelated"}:
                continue
            if verdict.confidence < min_rel:
                continue
            if verdict.relevance not in {
                RELEVANCE_SAME_COMPANY,
                RELEVANCE_EXTERNAL_VERIFIED,
                RELEVANCE_EXTERNAL_NEWS,
                "relevant_external_news",
            }:
                continue
            if not _hard_url_ok(url, snippet=verdict.reason):
                continue
            allowed_urls.add(url.lower())

        def _url_ok(url: str | None) -> bool:
            if not url:
                return False
            if not _hard_url_ok(url):
                return False
            host = _host(url)
            if domain and host and (host == domain or host.endswith("." + domain)):
                return True
            return url.lower() in allowed_urls

        result.evidence = [
            item
            for item in (result.evidence or [])
            if _url_ok(item.source_url)
            or (
                not item.source_url
                and is_source_relevant_to_prospect(
                    source_url=None,
                    company_name=company,
                    domain=domain,
                    source_note=item.note,
                )
            )
        ]

        # Decision maker — requires grounded supporting source + person/title checks.
        dm_name = ai.decision_maker_name
        dm_title = ai.decision_maker_title
        dm_src = ai.decision_maker_source_url
        if (
            dm_name
            and dm_title
            and ai.decision_maker_confidence >= min_dm
            and is_valid_person_name(dm_name, company_domain=domain)
            and is_credible_leadership_title(dm_title)
            and _url_ok(dm_src)
        ):
            result.decision_maker = DecisionMakerInfo(
                name=dm_name,
                title=dm_title,
                authority_type=None,
                is_founder_or_equivalent=bool(
                    any(
                        t in dm_title.lower()
                        for t in ("founder", "owner", "ceo", "president", "partner")
                    )
                ),
                confidence=ai.decision_maker_confidence,
                evidence=[
                    EvidenceItem(
                        source_url=dm_src,
                        source_type="ai_validation",
                        category="decision_maker",
                        note=f"AI-validated decision-maker: {dm_name} ({dm_title})",
                        confidence=ai.decision_maker_confidence,
                        checked_at=datetime.now(timezone.utc),
                    )
                ],
            )
        else:
            result.decision_maker = self._ground_decision_maker(
                result.decision_maker, company=company, domain=domain
            )

        # Contact — company-domain only + AI confidence + grounded source when provided.
        email = ai.contact_email
        contact_src_ok = (not ai.contact_source_url) or _url_ok(ai.contact_source_url)
        if (
            email
            and ai.contact_confidence >= min_fact
            and email_matches_company_domain(email, domain)
            and contact_src_ok
        ):
            result.contact = ContactPathInfo(
                email=email,
                email_confidence="inferred",
                company_phone=ai.contact_phone if ai.contact_confidence >= min_fact else None,
                other_contact_path=(result.contact.other_contact_path if result.contact else None),
                evidence=[
                    EvidenceItem(
                        source_url=ai.contact_source_url,
                        source_type="ai_validation",
                        category="contact",
                        note=f"AI-validated company contact ({email}).",
                        confidence=ai.contact_confidence,
                        checked_at=datetime.now(timezone.utc),
                    )
                ],
            )
        elif result.contact and result.contact.email:
            if not email_matches_company_domain(result.contact.email, domain):
                result.contact = ContactPathInfo(
                    email=None,
                    email_confidence=None,
                    company_phone=None,
                    other_contact_path=result.contact.other_contact_path,
                    evidence=[],
                )

        # Primary location ONLY when headquarters/primary and supporting source is grounded.
        # Expansion/acquisition/event locations become signals only — never primary city/state.
        if (
            ai.location_confidence >= min_fact
            and is_headquarters_location(ai.location_type)
            and _url_ok(ai.location_source_url)
        ):
            if ai.city:
                result.city = ai.city
            if ai.state:
                result.state = ai.state
            if ai.region:
                result.region = ai.region
        else:
            # Do not keep provider-guessed primary location from expansion articles.
            result.city = None
            result.state = None
            result.region = None
            if (
                ai.location_type
                and ai.location_type in {"office", "expansion", "acquisition", "event"}
                and (ai.city or ai.state)
                and ai.location_confidence >= min_fact
                and _url_ok(ai.location_source_url)
            ):
                # Preserve as non-primary location metadata when available.
                loc_entry = {
                    "city": ai.city,
                    "state": ai.state,
                    "region": ai.region,
                    "location_type": ai.location_type,
                    "source_url": ai.location_source_url,
                }
                existing_locs = list(result.locations or [])
                existing_locs.append(loc_entry)
                result.locations = existing_locs

        if ai.industry and ai.industry_confidence >= min_fact:
            result.industry = ai.industry

        # Signals — company-specific only, grounded source required.
        ai_signals: list[PublicSignal] = []
        for sig in ai.signals:
            if sig.confidence < min_fact:
                continue
            if not (sig.summary or "").strip():
                continue
            if not sig.source_url or not _url_ok(sig.source_url):
                continue
            ai_signals.append(
                PublicSignal(
                    text=sig.summary.strip()[:280],
                    category="timing",
                    confidence=sig.confidence,
                    evidence=[
                        EvidenceItem(
                            source_url=sig.source_url,
                            source_type="ai_validation",
                            category=sig.type or "timing",
                            note=sig.summary.strip()[:200],
                            confidence=sig.confidence,
                            checked_at=datetime.now(timezone.utc),
                        )
                    ],
                )
            )
        if ai_signals:
            result.signals = ai_signals
        else:
            result.signals = [
                s
                for s in (result.signals or [])
                if self._signal_relevant(s, company=company, domain=domain)
            ]

        note = (result.research_notes or "").strip()
        ai_note = (
            f"AI validation ({ai.model or 'gpt-4o-mini'}): {ai.reason or 'ok'} "
            f"(sent={ai.sources_sent}, hard_rejected={ai.sources_hard_rejected})."
        )
        result.research_notes = f"{note}\n{ai_note}".strip() if note else ai_note
        result.company_name = company
        return result

    def _ground_decision_maker(
        self,
        dm: DecisionMakerInfo | None,
        *,
        company: str,
        domain: str | None,
    ) -> DecisionMakerInfo | None:
        if dm is None or not dm.name or not dm.title:
            return None
        if not is_valid_person_name(dm.name, company_domain=domain):
            return None
        if not is_credible_leadership_title(dm.title):
            return None
        # Require at least one grounded supporting evidence URL.
        for item in dm.evidence or []:
            if item.source_url and is_source_relevant_to_prospect(
                source_url=item.source_url,
                company_name=company,
                domain=domain,
                source_note=item.note,
            ):
                return dm
        return None

    def _cleanup_polluted_evidence(
        self,
        prospect: Prospect,
        *,
        company: str,
        domain: str | None,
    ) -> dict[str, Any]:
        """Remove unrelated evidence and clear ungrounded DM / primary location."""
        removed_ids: list[str] = []
        sources = self.evidence.list_sources(prospect.id)
        to_delete: list[uuid.UUID] = []
        for row in sources:
            url = row.source_url
            note = row.note
            if url or note:
                if not is_source_relevant_to_prospect(
                    source_url=url,
                    company_name=company,
                    domain=domain,
                    source_note=note,
                ):
                    to_delete.append(row.id)
                    removed_ids.append(str(row.id))
        if to_delete:
            self.evidence.delete_sources(to_delete)

        # Re-load remaining sources after delete.
        remaining = self.evidence.list_sources(prospect.id)
        grounded_dm = False
        for row in remaining:
            if (row.category or "").lower() != "decision_maker":
                continue
            if row.source_url and is_source_relevant_to_prospect(
                source_url=row.source_url,
                company_name=company,
                domain=domain,
                source_note=row.note,
            ):
                grounded_dm = True
                break

        # Clear decision-maker unless still supported by a grounded source.
        # Valid person names from unrelated orgs (e.g. Donna Bailey @ cbhphilly) must go.
        if not grounded_dm:
            if (prospect.executive_name or "").strip().lower() != UNKNOWN_DECISION_MAKER.lower():
                prospect.executive_name = UNKNOWN_DECISION_MAKER
            prospect.executive_title = None
            prospect.authority_type = None
            prospect.is_founder_or_equivalent = None
        elif not is_valid_person_name(prospect.executive_name, company_domain=domain):
            prospect.executive_name = UNKNOWN_DECISION_MAKER
            prospect.executive_title = None

        # Clear primary location — only re-set when HQ evidence is confirmed later.
        cleared_location = bool(prospect.city or prospect.state or prospect.region)
        prospect.city = None
        prospect.state = None
        prospect.region = None

        # Drop signals that no longer look company-specific.
        if isinstance(prospect.public_signals, list):
            kept: list[Any] = []
            for sig in prospect.public_signals:
                text = str(sig).strip()
                if not text:
                    continue
                if is_source_relevant_to_prospect(
                    source_url=None,
                    company_name=company,
                    domain=domain,
                    source_note=text,
                ):
                    kept.append(text)
            prospect.public_signals = kept

        self.db.add(prospect)
        self.db.commit()

        return {
            "removed_source_count": len(removed_ids),
            "removed_source_ids": removed_ids,
            "cleared_decision_maker": not grounded_dm,
            "cleared_primary_location": cleared_location,
        }

    def _apply_research(self, prospect: Prospect, result: PublicResearchResult) -> bool:
        """Apply filtered research. Returns True when company identity is uncertain."""
        domain = normalize_domain(result.domain) or domain_from_website_or_email(
            website=result.website or prospect.website, email=None
        ) or normalize_domain(prospect.domain)

        preferred_company = prefer_canonical_company_name(
            current=prospect.company, candidate=result.company_name
        )
        if preferred_company and not is_generic_service_title(preferred_company):
            prospect.company = preferred_company
        elif result.company_name and (
            not prospect.company or is_generic_service_title(prospect.company)
        ):
            if not is_generic_service_title(result.company_name):
                prospect.company = result.company_name

        if result.website:
            prospect.website = result.website
        if domain:
            prospect.domain = domain
        if result.city:
            prospect.city = result.city
        if result.state:
            prospect.state = result.state
        if result.region:
            prospect.region = result.region
        if result.industry:
            prospect.industry = result.industry
        if result.company_description:
            if is_source_relevant_to_prospect(
                source_url=result.website or (f"https://{domain}" if domain else None),
                company_name=prospect.company,
                domain=domain,
                source_snippet=result.company_description,
                source_note=result.company_description,
            ) or (domain and result.domain and normalize_domain(result.domain) == domain):
                prospect.company_description = result.company_description
        if result.revenue_band:
            prospect.revenue_band = result.revenue_band
        if result.headcount_band:
            prospect.headcount_band = result.headcount_band
        if result.locations is not None:
            prospect.locations_json = result.locations
        if result.research_notes:
            prior = (prospect.research_notes or "").strip()
            prospect.research_notes = (
                f"{prior}\n{result.research_notes}".strip() if prior else result.research_notes
            )

        company = prospect.company

        dm = self._ground_decision_maker(
            result.decision_maker, company=company, domain=domain
        )
        if dm is not None and dm.name:
            prospect.executive_name = dm.name
            if dm.title:
                prospect.executive_title = dm.title
            if dm.authority_type:
                prospect.authority_type = dm.authority_type
            if dm.is_founder_or_equivalent is not None:
                prospect.is_founder_or_equivalent = dm.is_founder_or_equivalent
            if dm.linkedin_url:
                prospect.linkedin_url = dm.linkedin_url
            for item in dm.evidence:
                self._persist_evidence(prospect.id, item, company=company, domain=domain)
        if not is_valid_person_name(prospect.executive_name, company_domain=domain):
            if (prospect.executive_name or "").strip().lower() != UNKNOWN_DECISION_MAKER.lower():
                prospect.executive_name = UNKNOWN_DECISION_MAKER
                prospect.executive_title = None

        contact = result.contact
        if contact is not None:
            email = (contact.email or "").strip() or None
            conf = (contact.email_confidence or "").strip().lower() or None
            if email and not email_matches_company_domain(email, domain):
                email = None
            if email and conf in {"inferred", "unverified", "guess", "pattern"}:
                prospect.email = email
                prospect.email_confidence = "inferred"
            elif email and conf not in {"placeholder", "none", "missing"}:
                prospect.email = email
                prospect.email_confidence = conf or "unverified"
            elif not email or is_placeholder_email(prospect.email):
                if is_placeholder_email(prospect.email) or not (prospect.email or "").strip():
                    prospect.email = placeholder_email_for_domain(prospect.domain)
                    prospect.email_confidence = "placeholder"
            if contact.company_phone:
                prospect.company_phone = contact.company_phone
            if contact.other_contact_path and is_source_relevant_to_prospect(
                source_url=contact.other_contact_path,
                company_name=company,
                domain=domain,
            ):
                prospect.other_contact_path = contact.other_contact_path
            for item in contact.evidence:
                self._persist_evidence(prospect.id, item, company=company, domain=domain)

        relevant_signals = [
            signal
            for signal in (result.signals or [])
            if self._signal_relevant(signal, company=company, domain=domain)
        ]
        if relevant_signals:
            self._merge_signals(prospect, relevant_signals)
            for signal in relevant_signals:
                for item in signal.evidence:
                    self._persist_evidence(prospect.id, item, company=company, domain=domain)

        for item in result.evidence:
            self._persist_evidence(prospect.id, item, company=company, domain=domain)

        if not (prospect.executive_name or "").strip():
            prospect.executive_name = UNKNOWN_DECISION_MAKER
        elif not is_valid_person_name(prospect.executive_name, company_domain=domain):
            if prospect.executive_name.strip().lower() != UNKNOWN_DECISION_MAKER.lower():
                prospect.executive_name = UNKNOWN_DECISION_MAKER

        classification = getattr(result, "classification", None)
        if classification:
            prospect.classification_json = classification
            if classification.get("business_type"):
                prospect.business_type = str(classification["business_type"])[:128]

        identity_uncertain = bool(result.identity_uncertain) or is_generic_service_title(
            prospect.company
        )
        return identity_uncertain

    def _signal_relevant(
        self, signal: PublicSignal, *, company: str, domain: str | None
    ) -> bool:
        if not signal.evidence:
            return is_source_relevant_to_prospect(
                source_url=None,
                company_name=company,
                domain=domain,
                source_note=signal.text,
            )
        return any(
            is_source_relevant_to_prospect(
                source_url=item.source_url,
                company_name=company,
                domain=domain,
                source_note=item.note or signal.text,
            )
            for item in signal.evidence
        )

    def _merge_signals(self, prospect: Prospect, signals: list[PublicSignal]) -> None:
        existing: list[Any] = []
        if isinstance(prospect.public_signals, list):
            existing = list(prospect.public_signals)
        elif isinstance(prospect.public_signals, dict):
            existing = [f"{k}: {v}" for k, v in prospect.public_signals.items()]
        texts = {str(x).strip() for x in existing if str(x).strip()}
        for signal in signals:
            text = (signal.text or "").strip()
            if not text or text in texts:
                continue
            lowered = text.lower()
            if "has an employee capacity problem" in lowered or "company has a problem" in lowered:
                continue
            existing.append(text)
            texts.add(text)
        prospect.public_signals = existing

    def _persist_evidence(
        self,
        prospect_id: uuid.UUID,
        item: EvidenceItem,
        *,
        company: str,
        domain: str | None,
    ) -> None:
        # Never persist AI helper notes without a company-bound URL unless hard-relevant.
        if item.source_type == "ai_validation" and not item.source_url:
            if item.category not in {"decision_maker", "contact"}:
                if not is_source_relevant_to_prospect(
                    source_url=None,
                    company_name=company,
                    domain=domain,
                    source_note=item.note,
                ):
                    return
        if item.source_url or item.note:
            if item.source_url and not is_source_relevant_to_prospect(
                source_url=item.source_url,
                company_name=company,
                domain=domain,
                source_note=item.note,
            ):
                return
            if not item.source_url and item.category not in {"decision_maker", "contact"}:
                if not is_source_relevant_to_prospect(
                    source_url=None,
                    company_name=company,
                    domain=domain,
                    source_note=item.note,
                ):
                    return
        self.evidence.add_or_update_source(
            prospect_id,
            source_url=item.source_url,
            source_type=item.source_type or "other",
            category=item.category,
            note=item.note,
            confidence=item.confidence,
            checked_at=item.checked_at,
        )


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return normalize_domain(host) or host or None
    except Exception:  # noqa: BLE001
        return None
