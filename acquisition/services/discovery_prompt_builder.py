"""Prompt builders for agentic OpenAI discovery (Stage A) and enrichment (Stage B)."""

from __future__ import annotations

import json
from typing import Any


DISCOVERY_INSTRUCTIONS = """You are FreedomBot's master B2B prospect discovery agent (Stage A).

Your goal is NOT to return the first REQUESTED_COUNT companies that vaguely match the ICP.

Your goal is to search broadly enough to identify, research, compare, and return the
strongest REQUESTED_COUNT companies that appear HIGHLY LIKELY to become READY FOR
OUTREACH after deeper slot verification.

Research more companies than you ultimately return when necessary.
Reject weak or incomplete candidates internally and continue searching.
Only return candidates with strong preliminary evidence across mandatory
outreach-readiness dimensions (company identity, ICP/scale, decision-maker,
contactability for the configured channel, researchability, useful timing signals).

Do NOT perform authoritative Priority Score math — backend owns that.
Do NOT mark seeds as finally ready — slots perform deep proof; backend is final authority.

Never invent companies, websites, domains, emails, revenue, or decision-makers.
Never bypass login walls or authentication restrictions.
Unknown/null is required when evidence is insufficient.

Return ONLY valid JSON matching the required schema. No markdown fences.
"""

ENRICHMENT_INSTRUCTIONS = """You are FreedomBot's Stage B prospect enrichment agent.

Deeply research ONE company at a time using public web sources.

Investigate the official website (home, about, leadership/team, services, contact, locations, careers, news/blog),
publicly accessible professional/company pages, registries, directories, news, job postings, and other credible sources.

Decision-maker identification is high priority: Founder, Owner, CEO, President, Managing Partner/Director,
or the highest practical authority. Prefer official leadership pages, then public professional profiles,
then reputable secondary sources. If not found after reasonable research, return null — never guess.

Never invent emails from name/domain patterns. Never bypass login walls or access controls.

Use the supplied scoring definitions to return Fit, Timing/Need, and Reachability with reasons.
Do not compute the weighted Priority Score.

Return ONLY valid JSON matching the required schema. No markdown fences.
"""

SLOT_INSTRUCTIONS = """You are FreedomBot's outreach-ready ProspectSlot agent.

Your job is to return EXACTLY ONE final B2B prospect that satisfies ALL mandatory
ready-for-outreach requirements for the configured outreach channel — or explicitly
report that the slot is exhausted after MAX_CANDIDATES_PER_SLOT attempts.

Start by deeply researching the assigned seed company. Do not accept it merely because
it looks plausible. Verify ICP fit, exclusions, scale, decision-maker, contact path,
scoring, and evidence using credible public web sources.

If the seed fails ANY mandatory readiness requirement, reject it with an explicit reason
and continue searching for a DIFFERENT ICP-matching company (not in ALREADY_FOUND_OR_IN_PROGRESS).
Research each alternative deeply inside this same agentic session using web_search.

Never fabricate emails, companies, or facts. Never bypass login walls.
Do not compute the weighted Priority Score — only Fit, Timing/Need, Reachability.

Return ONLY valid JSON matching the slot result schema. No markdown fences.
"""

# Backward-compatible alias used by older one-shot path / tests.
SYSTEM_INSTRUCTIONS = ENRICHMENT_INSTRUCTIONS


def _pct_weights(score_weights: dict[str, Any]) -> dict[str, float]:
    return {
        "fit": float(score_weights.get("fit", 0.45)) * 100.0,
        "timing": float(score_weights.get("timing", 0.25)) * 100.0,
        "reachability": float(score_weights.get("reachability", 0.30)) * 100.0,
        "ready_for_outreach_min_priority": float(
            score_weights.get("ready_for_outreach_min_priority", 55)
        ),
        "ready_for_outreach_min_reachability": float(
            score_weights.get("ready_for_outreach_min_reachability", 40)
        ),
    }


class DiscoveryPromptBuilder:
    """Assemble Stage A discovery and Stage B enrichment prompts from Admin + context."""

    @classmethod
    def build(
        cls,
        admin_settings: dict[str, Any],
        scoring_settings: dict[str, Any] | None,
        coverage_context: dict[str, Any] | None,
        previous_prospect_context: dict[str, Any] | None,
        requested_count: int,
    ) -> dict[str, str]:
        """Legacy one-shot builder (kept for tests / fallback). Prefer build_discovery + build_enrichment."""
        return cls.build_enrichment_batch_prompt(
            admin_settings,
            scoring_settings,
            coverage_context,
            previous_prospect_context,
            requested_count,
        )

    @classmethod
    def build_discovery(
        cls,
        admin_settings: dict[str, Any],
        coverage_context: dict[str, Any] | None,
        *,
        remaining_count: int,
        already_found: list[dict[str, str]] | None = None,
        attempt: int = 1,
        max_attempts: int = 3,
        reserved_domains: list[str] | None = None,
        scoring_settings: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Master Stage A: return strongest provisional outreach-ready seeds."""
        geography = admin_settings.get("geography") or {}
        revenue = admin_settings.get("revenue") or {}
        coverage = coverage_context or {}
        found = already_found or []
        weights = scoring_settings or admin_settings.get("score_weights") or {}
        pct = _pct_weights(weights if isinstance(weights, dict) else {})
        channel = str(
            (weights if isinstance(weights, dict) else {}).get("outreach_channel")
            or admin_settings.get("outreach_channel")
            or "email"
        )
        allow_generic = bool(
            (weights if isinstance(weights, dict) else {}).get("allow_generic_inbox", False)
        )
        known = list(reserved_domains or [])
        if not known:
            known = list(coverage.get("do_not_rediscover_domains") or [])[
                :200
            ] + list(coverage.get("known_domains") or [])[:200]
        # Dedupe preserve order
        seen_d: set[str] = set()
        known_unique: list[str] = []
        for d in known:
            nd = str(d or "").strip().lower()
            if not nd or nd in seen_d:
                continue
            seen_d.add(nd)
            known_unique.append(nd)

        sections = [
            "ROLE\nYou are FreedomBot's master B2B prospect discovery agent (Stage A).",
            (
                "OBJECTIVE\n"
                f"Return the strongest REMAINING_COUNT={int(remaining_count)} companies that already "
                "appear highly likely to satisfy FINAL outreach-readiness requirements for the "
                f"configured channel ({channel}).\n"
                f"This is master-discovery attempt {int(attempt)} of {int(max_attempts)}.\n"
                "Do NOT return the first N vaguely-plausible ICP matches.\n"
                "Search broadly, research multiple possible companies, reject weak candidates "
                "internally, compare viable candidates, then return only the strongest seeds.\n"
                "Investigate more companies than you return when needed. Quality over quota filling.\n"
                "Never fabricate prospects to fill the count. Prefer a shortfall over weak seeds."
            ),
            (
                "BUSINESS OBJECTIVE\n"
                + json.dumps(
                    {
                        "notes": admin_settings.get("notes") or "",
                        "discovery_cycle_limit": admin_settings.get("discovery_cycle_limit"),
                        "outreach_channel": channel,
                        "allow_generic_inbox": allow_generic,
                    },
                    indent=2,
                )
            ),
            cls._section_icp(admin_settings),
            cls._section_categories(admin_settings),
            cls._section_geography(geography),
            cls._section_revenue(revenue),
            cls._section_exclusions(admin_settings),
            cls._section_scoring(pct, admin_settings),
            cls._section_outreach(pct),
            (
                "OUTREACH-READY PRELIMINARY GATES (Stage A quality bar)\n"
                f"Channel={channel}. allow_generic_inbox={allow_generic}.\n"
                "Before returning a seed, establish strong preliminary confidence in:\n"
                "1) COMPANY — real company, official website/domain, geography match, "
                "category/industry match, no configured exclusion.\n"
                "2) ICP/SCALE — credible ICP fit; sufficient apparent size/scale "
                "(revenue/headcount/locations/organization signals when available; never invent).\n"
                "3) DECISION MAKER — founder/owner/CEO/president/managing partner/"
                "highest practical authority appears identifiable with leadership evidence.\n"
                "4) CONTACTABILITY — for the configured channel, strong likelihood of a "
                "legitimate contact path. For email: prefer public business/direct executive "
                "email evidence; never invent patterns; classify generic inboxes separately.\n"
                "5) RESEARCHABILITY — enough credible public information for deeper verification "
                "(reject near-dead-ends with almost no evidence).\n"
                "6) TIMING/NEED — useful public signals when available (hiring, growth, expansion, "
                "leadership changes, new services, acquisition activity, organizational complexity).\n"
                "7) SOURCE QUALITY — prefer official company sources, then accessible professional "
                "pages, registries, reputable news, job postings, credible directories.\n"
                "Reject weak candidates internally (too small, excluded, no leadership, no contact "
                "path, weak ICP evidence) and continue searching. Compare viable candidates on "
                "ICP strength, category, geography, scale, DM availability, contactability, "
                "timing signals, source quality, and completeness before final selection."
            ),
            cls._section_sources(),
            cls._section_previous(coverage, coverage),
            (
                "DO NOT REDISCOVER / ALREADY KNOWN OR IN PROGRESS\n"
                "Do not waste research selecting companies/domains already known, opted out, "
                "existing customers where prohibited, active outreach, or current-run reserved "
                "when they are not eligible for rediscovery.\n"
                + json.dumps(
                    {
                        "already_found_this_run": found[:80],
                        "do_not_rediscover_domains": known_unique[:250],
                        "in_progress_or_active_domains": list(
                            coverage.get("in_progress_or_active_domains") or []
                        )[:80],
                        "opt_out_or_excluded_domains": list(
                            coverage.get("opt_out_or_excluded_domains") or []
                        )[:80],
                    },
                    indent=2,
                )
            ),
            (
                "REQUIRED OUTPUT\n"
                "Return JSON:\n"
                "{\n"
                '  "prospects": [ ... up to '
                + str(int(remaining_count))
                + " STRONG seed objects ... ],\n"
                '  "discovery_meta": {\n'
                '    "companies_considered": <int if reliably known, else null>,\n'
                '    "rejected_before_selection": [\n'
                '      {"company_name","domain","reason_code","reason"}\n'
                "    ],\n"
                '    "rejection_reason_counts": [{"reason_code": string, "count": integer}]\n'
                "  }\n"
                "}\n"
                "Each seed must include:\n"
                "company_name, official_website (or website), normalized_domain, "
                "location{city,state,country}, industry, business_type, description, "
                "preliminary_scale_evidence{revenue,headcount,other_signals[]}, "
                "preliminary_decision_maker{name,title,source_urls[]}, "
                "preliminary_contactability{"
                "direct_business_email_found,generic_email_found,phone_found,contact_page_found"
                "}, "
                "timing_signals[], why_strong_seed, "
                "initial_evidence_urls[], sources[{url,type,supports}].\n"
                "Provisional fields are preliminary only — do not pretend they are fully verified.\n"
                "Do NOT return Fit/Timing/Reachability component scores or Priority Score here.\n"
                "Only include companies_considered / rejection tallies you can honestly support "
                "from this session's research; otherwise set companies_considered to null."
            ),
            cls._section_no_hallucination(),
        ]
        return {"instructions": DISCOVERY_INSTRUCTIONS, "prompt": "\n\n".join(sections)}

    @classmethod
    def build_enrichment(
        cls,
        admin_settings: dict[str, Any],
        scoring_settings: dict[str, Any] | None,
        candidate: dict[str, Any],
    ) -> dict[str, str]:
        weights = scoring_settings or admin_settings.get("score_weights") or {}
        pct = _pct_weights(weights if isinstance(weights, dict) else {})
        geography = admin_settings.get("geography") or {}
        revenue = admin_settings.get("revenue") or {}
        sections = [
            "ROLE\nYou are FreedomBot's Stage B prospect enrichment agent for ONE company.",
            (
                "TARGET COMPANY\n"
                + json.dumps(
                    {
                        "company_name": candidate.get("company_name"),
                        "website": candidate.get("website"),
                        "normalized_domain": candidate.get("normalized_domain"),
                        "location": candidate.get("location"),
                        "industry": candidate.get("industry"),
                        "business_type": candidate.get("business_type"),
                        "description": candidate.get("description"),
                        "initial_evidence_urls": candidate.get("initial_evidence_urls") or [],
                    },
                    indent=2,
                )
            ),
            cls._section_icp(admin_settings),
            cls._section_categories(admin_settings),
            cls._section_geography(geography),
            cls._section_revenue(revenue),
            cls._section_exclusions(admin_settings),
            cls._section_scoring(pct, admin_settings),
            cls._section_outreach(pct),
            cls._section_enrichment_research(),
            cls._section_sources(),
            cls._section_verification(),
            cls._section_enrichment_schema(),
            cls._section_no_hallucination(),
        ]
        return {"instructions": ENRICHMENT_INSTRUCTIONS, "prompt": "\n\n".join(sections)}

    @classmethod
    def build_outreach_slot(
        cls,
        admin_settings: dict[str, Any],
        scoring_settings: dict[str, Any] | None,
        *,
        slot_id: str,
        seed: dict[str, Any] | None,
        reserved_domains: list[str],
        max_candidates: int,
        coverage_context: dict[str, Any] | None = None,
        repair_notes: list[str] | None = None,
    ) -> dict[str, str]:
        """One agentic call: research seed, reject if needed, find a ready replacement."""
        weights = scoring_settings or admin_settings.get("score_weights") or {}
        pct = _pct_weights(weights if isinstance(weights, dict) else {})
        geography = admin_settings.get("geography") or {}
        revenue = admin_settings.get("revenue") or {}
        channel = str(weights.get("outreach_channel") or "email")
        allow_generic = bool(weights.get("allow_generic_inbox", False))
        coverage = coverage_context or {}
        seed = seed or {}
        sections = [
            "ROLE\nYou are FreedomBot's outreach-ready ProspectSlot agent.",
            (
                "OBJECTIVE\n"
                f"Slot {slot_id}: produce exactly ONE final prospect ready for outreach channel={channel}.\n"
                f"MAX_CANDIDATES_PER_SLOT={int(max_candidates)}.\n"
                "Research the seed first. If it fails any mandatory requirement, reject it and "
                "search/research alternatives inside this same session until one passes or the "
                "candidate limit is reached. Never return an incomplete seed just to fill the slot."
            ),
            # Stable Admin context first (prompt-cache friendly), then per-slot seed/reserved.
            cls._section_icp(admin_settings),
            cls._section_categories(admin_settings),
            cls._section_geography(geography),
            cls._section_revenue(revenue),
            cls._section_exclusions(admin_settings),
            cls._section_scoring(pct, admin_settings),
            cls._section_outreach(pct),
            (
                "OUTREACH-READY MANDATORY GATES\n"
                f"Channel={channel}. allow_generic_inbox={allow_generic}.\n"
                "Ready requires: verified company/domain, ICP match, no exclusions, "
                "supported scale evidence (or Admin allow_strong_fit_outside_band with strong fit), "
                "identified decision-maker, legitimate contact path, personalization context, "
                "Fit/Timing/Reachability scores with reasons, and source URLs for important facts.\n"
                "For email channel: a verified business email is mandatory. "
                "Never invent email patterns. Classify generic inboxes (info@, hello@, etc.) separately."
            ),
            cls._section_enrichment_research(),
            cls._section_sources(),
            cls._section_verification(),
            cls._section_previous(coverage, coverage),
            (
                "SEED COMPANY\n"
                + json.dumps(
                    {
                        "company_name": seed.get("company_name"),
                        "website": seed.get("website") or seed.get("official_website"),
                        "normalized_domain": seed.get("normalized_domain"),
                        "location": seed.get("location"),
                        "industry": seed.get("industry"),
                        "business_type": seed.get("business_type"),
                        "description": seed.get("description"),
                        "initial_evidence_urls": seed.get("initial_evidence_urls") or [],
                        "preliminary_scale_evidence": seed.get("preliminary_scale_evidence"),
                        "preliminary_decision_maker": seed.get("preliminary_decision_maker"),
                        "preliminary_contactability": seed.get("preliminary_contactability"),
                        "timing_signals": seed.get("timing_signals") or [],
                        "why_strong_seed": seed.get("why_strong_seed"),
                        "sources": seed.get("sources") or [],
                    },
                    indent=2,
                )
            ),
            (
                "ALREADY_FOUND_OR_IN_PROGRESS — DO NOT RETURN OR RESEARCH AS REPLACEMENTS\n"
                + json.dumps(sorted({d for d in reserved_domains if d})[:200], indent=2)
            ),
            (
                "REQUIRED OUTPUT\n"
                "Return JSON object:\n"
                '{\n'
                f'  "slot_id": "{slot_id}",\n'
                '  "status": "ready|exhausted|technical_failure",\n'
                '  "candidates_attempted": <int>,\n'
                '  "rejected_candidates": [{"company_name","domain","reason_code","reason"}],\n'
                '  "prospect": <enriched prospect object or null>,\n'
                '  "failure_reason": <string|null>\n'
                "}\n"
                "When status=ready, prospect must include the Stage B enrichment fields plus "
                "outreach_readiness{ai_recommended_ready,channel,company_verified,icp_verified,"
                "scale_supported,decision_maker_verified,contact_path_verified,"
                "personalization_context_available,mandatory_requirements_passed,reason}.\n"
                "Scores must be integers 0-100 (never arrays or objects like {score, reason}). "
                "Put the explanation in fit_reason / timing_reason / reachability_reason fields. "
                "source_urls_consulted must be an integer count."
            ),
            cls._section_no_hallucination(),
        ]
        if repair_notes:
            sections.append(
                "REPAIR INSTRUCTION\nThe previous structured response was invalid:\n- "
                + "\n- ".join(repair_notes[-8:])
                + "\nReturn the COMPLETE slot result again conforming exactly to the schema."
            )
        return {"instructions": SLOT_INSTRUCTIONS, "prompt": "\n\n".join(sections)}

    @classmethod
    def build_enrichment_batch_prompt(
        cls,
        admin_settings: dict[str, Any],
        scoring_settings: dict[str, Any] | None,
        coverage_context: dict[str, Any] | None,
        previous_prospect_context: dict[str, Any] | None,
        requested_count: int,
    ) -> dict[str, str]:
        """Legacy one-shot prompt (tests / fallback)."""
        weights = scoring_settings or admin_settings.get("score_weights") or {}
        pct = _pct_weights(weights if isinstance(weights, dict) else {})
        geography = admin_settings.get("geography") or {}
        revenue = admin_settings.get("revenue") or {}
        coverage = coverage_context or {}
        previous = previous_prospect_context or coverage
        sections = [
            cls._section_role(),
            cls._section_objective(requested_count),
            cls._section_icp(admin_settings),
            cls._section_categories(admin_settings),
            cls._section_geography(geography),
            cls._section_revenue(revenue),
            cls._section_exclusions(admin_settings),
            cls._section_scoring(pct, admin_settings),
            cls._section_outreach(pct),
            cls._section_previous(previous, coverage),
            cls._section_research_requirements(),
            cls._section_sources(),
            cls._section_verification(),
            cls._section_output_schema(requested_count),
            cls._section_no_hallucination(),
            cls._section_run_objective(requested_count, admin_settings),
        ]
        return {"instructions": SYSTEM_INSTRUCTIONS, "prompt": "\n\n".join(sections)}

    @staticmethod
    def _section_role() -> str:
        return "ROLE\nYou are FreedomBot's acquisition discovery and research agent."

    @staticmethod
    def _section_objective(requested_count: int) -> str:
        return (
            "BUSINESS OBJECTIVE\n"
            f"Return up to {int(requested_count)} high-quality ICP-fit prospect records "
            "with evidence-backed research suitable for responsible B2B outreach evaluation."
        )

    @staticmethod
    def _section_icp(admin: dict[str, Any]) -> str:
        return (
            "FULL ICP\n"
            + json.dumps(
                {
                    "buyer_titles": admin.get("buyer_titles") or [],
                    "fit_themes": admin.get("fit_themes") or [],
                    "notes": admin.get("notes") or "",
                    "discovery_cycle_limit": admin.get("discovery_cycle_limit"),
                },
                indent=2,
            )
        )

    @staticmethod
    def _section_categories(admin: dict[str, Any]) -> str:
        return (
            "TARGET CATEGORIES\n"
            + json.dumps(
                {
                    "priority_categories": admin.get("priority_categories") or [],
                    "category_preferences": admin.get("category_preferences")
                    or admin.get("priority_categories")
                    or [],
                    "category_weights": admin.get("category_weights") or {},
                },
                indent=2,
            )
        )

    @staticmethod
    def _section_geography(geography: dict[str, Any]) -> str:
        return "GEOGRAPHY\n" + json.dumps(geography, indent=2)

    @staticmethod
    def _section_revenue(revenue: dict[str, Any]) -> str:
        return (
            "REVENUE/SCALE RULES\n"
            + json.dumps(revenue, indent=2)
            + "\nIf revenue cannot be reasonably supported, return status unknown/null. "
            "Do NOT invent revenue from headcount. Use headcount/locations/organization "
            "signals as alternate scale evidence only when Admin rules permit."
        )

    @staticmethod
    def _section_exclusions(admin: dict[str, Any]) -> str:
        return (
            "EXCLUSIONS\n"
            + json.dumps(
                {
                    "excluded_categories": admin.get("excluded_categories") or [],
                    "excluded_business_types": admin.get("excluded_business_types") or [],
                },
                indent=2,
            )
            + "\nApply exclusions strictly. Do not return excluded company types as eligible prospects."
        )

    @staticmethod
    def _section_scoring(pct: dict[str, float], admin: dict[str, Any]) -> str:
        return (
            "SCORING RULES\n"
            "Return numeric Fit, Timing/Need, and Reachability scores from 0-100 with plain-English reasons "
            "based only on researched evidence.\n"
            f"- Fit ({pct['fit']:.0f}% of Priority): how strongly the company matches the ICP "
            f"(buyer titles {admin.get('buyer_titles')}, fit themes {admin.get('fit_themes')}, "
            f"priority categories {admin.get('priority_categories')}, geography, revenue rules).\n"
            f"- Timing/Need ({pct['timing']:.0f}% of Priority): strength of public business signals "
            "(growth, hiring, expansion, leadership changes, new offerings, operational complexity).\n"
            f"- Reachability ({pct['reachability']:.0f}% of Priority): how realistically we can contact "
            "the correct decision-maker through a legitimate business channel "
            "(published business email, phone, or contact page — never invented).\n"
            "Do NOT invent evidence to justify a score. Do NOT compute the weighted Priority Score."
        )

    @staticmethod
    def _section_outreach(pct: dict[str, float]) -> str:
        return (
            "OUTREACH THRESHOLDS (informational — backend enforces)\n"
            + json.dumps(
                {
                    "ready_for_outreach_min_priority": pct["ready_for_outreach_min_priority"],
                    "ready_for_outreach_min_reachability": pct["ready_for_outreach_min_reachability"],
                },
                indent=2,
            )
            + "\nBackend readiness requires score thresholds AND mandatory contact/DM rules "
            "for the configured outreach channel. "
            "Phone/contact page alone without required email/DM (per Admin) is not enough for READY."
        )

    @staticmethod
    def _section_previous(previous: dict[str, Any], coverage: dict[str, Any]) -> str:
        payload = {
            "guidance": previous.get("guidance") or coverage.get("guidance"),
            "category_coverage": coverage.get("category_coverage")
            or previous.get("category_coverage")
            or {},
            "state_coverage": coverage.get("state_coverage") or previous.get("state_coverage") or {},
            "commonly_missing_fields": coverage.get("commonly_missing_fields")
            or previous.get("commonly_missing_fields")
            or {},
            "previous_prospects_sample": (previous.get("previous_prospects") or [])[:25],
            "total_discovered_prospects": previous.get("total_discovered_prospects")
            or coverage.get("total_discovered_prospects")
            or 0,
        }
        return "PREVIOUS DISCOVERY CONTEXT\n" + json.dumps(payload, indent=2)

    @staticmethod
    def _section_research_requirements() -> str:
        return (
            "RESEARCH REQUIREMENTS\n"
            "For each prospect research as available: company name, website/domain, city, state, country, "
            "industry/business type, description, revenue band if supportable, headcount if supportable, "
            "locations, decision-maker name/title/relationship/authority/public profile, business email if "
            "actually found (with source), phone, contact page, timing signals, and evidence URLs.\n"
            "If decision-maker cannot be confirmed, return unknown rather than guessing."
        )

    @staticmethod
    def _section_enrichment_research() -> str:
        return (
            "ENRICHMENT SEARCH STRATEGY\n"
            "A) Official company website — home, about, leadership/team, services, contact, locations, careers, news/blog.\n"
            "B) Public professional/company pages — publicly accessible LinkedIn company/executive results only "
            "(do not bypass authentication).\n"
            "C) Other credible public sources — registries, directories, news/press, interviews, job postings, "
            "public executive biographies.\n"
            "Perform additional searches when an important field is still missing.\n"
            "Decision-maker search order: Founder → Owner → CEO → President → Managing Partner → Managing Director "
            "→ other highest practical authority.\n"
            "Set decision_maker.search_attempted=true and include decision_maker.source_urls and confidence.\n"
            "Contact: only publish business email if publicly found with source; never invent from name/domain."
        )

    @staticmethod
    def _section_sources() -> str:
        return (
            "SOURCE PREFERENCE\n"
            "1) Official company website (About, Services, Leadership/Team, Contact, Locations, Careers)\n"
            "2) Public professional/company pages (publicly accessible LinkedIn only)\n"
            "3) Government/public registries where relevant\n"
            "4) Trusted public business directories\n"
            "5) Reputable news and press releases\n"
            "6) Public job postings/careers pages\n"
            "7) Public company social pages where useful\n"
            "8) Other credible public web sources\n"
            "Never bypass login walls or access restrictions. Never scrape private/restricted LinkedIn data."
        )

    @staticmethod
    def _section_verification() -> str:
        return (
            "VERIFICATION RULES\n"
            "Cross-check important identity facts. Prefer official company sources over random directories. "
            "Mark field statuses as verified|supported|estimated|unknown. Unknown must not be replaced with guesses."
        )

    @staticmethod
    def _section_output_schema(requested_count: int) -> str:
        n = int(requested_count)
        return (
            "REQUIRED OUTPUT FIELDS\n"
            'Return JSON object: {"prospects": [ ... up to '
            + str(n)
            + " items ... ]}\n"
            "Each prospect must include: company_name, website, normalized_domain, "
            "location{city,state,country}, industry, business_type, description, "
            "revenue{value_or_band,status,source_urls}, "
            "headcount{value_or_band,status,source_urls}, "
            "decision_maker{name,title,relationship,authority_level,profile_url,source_urls,confidence,search_attempted}, "
            "contact{business_email,email_status,phone,contact_url,source_urls}, "
            "timing_signals[], research_notes, research_completeness{}, fit_score, fit_reason, timing_score, "
            "timing_reason, reachability_score, reachability_reason, "
            "sources[{url,type,supports}].\n"
            "Scores must be numeric 0-100."
        )

    @staticmethod
    def _section_enrichment_schema() -> str:
        return (
            "REQUIRED OUTPUT\n"
            "Return a single JSON prospect object (not an array) with: "
            "company_name, website, normalized_domain, location{city,state,country}, industry, business_type, "
            "description, "
            "revenue{exact_amount:number|null, band:string|null, status, sources[]}, "
            "headcount{exact_value:integer|null, band:string|null, status, sources[]}, "
            "decision_maker{name,title,relationship,authority_level,profile_url,sources[],status,confidence,search_attempted}, "
            "contact{business_email,email_status,phone,contact_url,sources[],status}, timing_signals[], research_notes, "
            "research_completeness{company_identity,website,location,industry,revenue,headcount,decision_maker,"
            "contact,timing_signals,source_urls_consulted:integer,overall}, "
            "fit_score:integer|null (0-100, NOT an array), fit_reason, "
            "timing_score:integer|null (0-100, NOT an array), timing_reason, "
            "reachability_score:integer|null (0-100, NOT an array), reachability_reason, "
            "sources[{url,type,supports}].\n"
            "Statuses use verified|supported|estimated|unknown. Unknown fields must be null — never invent."
        )

    @staticmethod
    def _section_no_hallucination() -> str:
        return (
            "NO-HALLUCINATION RULES\n"
            "Never invent emails, revenue, decision-makers, or websites. "
            "Unknown/null is required when evidence is insufficient. "
            "Do not derive fake revenue from headcount."
        )

    @staticmethod
    def _section_run_objective(requested_count: int, admin: dict[str, Any]) -> str:
        cats = ", ".join(str(c) for c in (admin.get("priority_categories") or [])[:8]) or "ICP categories"
        return (
            "RUN-SPECIFIC OBJECTIVE\n"
            f"Discover and fully research up to {int(requested_count)} companies across: {cats}. "
            "Prefer quality and evidence over filling the quota with weak matches."
        )
