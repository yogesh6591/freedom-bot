"""Discovery provider (DISCOVERY_PROVIDER=multi).

Each run:
1. Plans slices from the slice ledger (never the same question twice in a row).
2. Harvests companies via OpenAI web search.
3. Filters cheaply: directory/review/social/news domains, "Top 10" list pages,
   excluded business types (word-boundary match on the firm's own name /
   description — never on who it serves), non-US.
4. Skips companies already in the database that were researched recently
   (no repeat research cost; they do not count as new).
5. Verifies AI-suggested websites are real and belong to the company.
6. Returns only new (or stale, due-for-refresh) candidates to orchestration.

Every step is written to the live trace when a trace context is open.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.base import DiscoveryProviderError
from acquisition.integrations.discovery.sources.base import HarvestResult
from acquisition.integrations.discovery.sources.openai_web import OpenAIWebClient, OpenAIWebHarvester
from acquisition.integrations.discovery.types import DiscoveryCandidate, ProviderStatus
from acquisition.models.prospect import AcquisitionStatus, Prospect
from acquisition.services import live_trace
from acquisition.services.discovery_candidate_triage_service import (
    DiscoveryCandidateTriageService,
    global_triage_max_candidates,
    serialize_candidate_for_pool,
)
from acquisition.services.discovery_slice_service import DiscoverySliceService, buckets_from_icp
from acquisition.services.domain_tools import is_non_company_domain, looks_like_listicle, root_domain
from acquisition.services.prospect_identity import find_existing_company_or_prospect, normalize_company_name
from acquisition.services.website_verifier import WebsiteVerifier

logger = logging.getLogger(__name__)

# Single active discovery source. NPPES / Exa removed from product path.
SUPPORTED_SOURCES: tuple[str, ...] = ("openai_web",)

SOURCE_LABELS = {
    "openai_web": "OpenAI web search",
    # Legacy labels kept so old Live Trace / prospect rows still render.
    "nppes": "NPPES provider registry",
    "exa": "Exa company search",
}
# Per-run fetch budgets (NOT final prospect counts).
DEFAULT_FETCH_LIMITS = {"openai_web": 10}
FETCH_LIMIT_BOUNDS = {
    "openai_web": (1, 20),
}


def clamp_fetch_limit(source: str, value: Any, *, default: int | None = None) -> int:
    lo, hi = FETCH_LIMIT_BOUNDS.get(source, (1, 100))
    fallback = DEFAULT_FETCH_LIMITS.get(source, lo) if default is None else int(default)
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = fallback
    return max(lo, min(hi, n))


def default_source_config(settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    enabled = {s.strip() for s in (cfg.discovery_sources_default or "").split(",") if s.strip()}
    # Only supported sources are configurable; ignore legacy nppes/exa defaults.
    return {
        name: {
            "enabled": (not enabled) or (name in enabled),
            "fetch_limit": DEFAULT_FETCH_LIMITS[name],
        }
        for name in SUPPORTED_SOURCES
    }


def merged_source_config(raw: dict[str, Any] | None, settings: Settings | None = None) -> dict[str, Any]:
    """Merge stored discovery_sources. Legacy nppes/exa keys are ignored."""
    base = default_source_config(settings)
    for name, val in (raw or {}).items():
        if name not in base or not isinstance(val, dict):
            continue
        base[name]["enabled"] = bool(val.get("enabled", base[name]["enabled"]))
        if "fetch_limit" in val and val.get("fetch_limit") is not None:
            base[name]["fetch_limit"] = clamp_fetch_limit(name, val.get("fetch_limit"))
    # Always keep OpenAI web present and enabled if nothing else is on.
    if not any(v.get("enabled") for v in base.values()):
        base["openai_web"]["enabled"] = True
    return base


def resolve_openai_web_client(db: Session | None, settings: Settings) -> OpenAIWebClient:
    api_key: str | None = None
    model = (settings.openai_web_model or "").strip()
    if db is not None:
        try:
            from acquisition.services.openai_service import OpenAIService

            api_key, int_model = OpenAIService(db=db, settings=settings).resolve_credentials()
            model = model or int_model
        except Exception:  # noqa: BLE001
            api_key = None
    if not api_key:
        api_key = (settings.openai_api_key or "").strip() or None
    return OpenAIWebClient(
        api_key=api_key,
        model=model or (settings.openai_model or "gpt-4o-mini"),
        settings=settings,
    )


def _exclusion_hit(excluded: list[str], *texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for ex in excluded:
        e = (ex or "").strip().lower()
        if not e:
            continue
        # "law firm" must match "law firm"/"law firms", not "…serve law firms…" in a
        # description about clients — so only names/types are passed in here.
        if re.search(rf"\b{re.escape(e)}s?\b", blob):
            return ex
    return None


@dataclass
class RunStats:
    harvest_cost_usd: float = 0.0
    by_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    slices: list[dict[str, Any]] = field(default_factory=list)
    rejected: int = 0
    known_skipped: int = 0
    verify_failed: int = 0
    duplicates_in_run: int = 0
    evaluated: int = 0
    requested: int = 0
    pool_target: int = 0
    pool_available: int = 0
    pool_before_cap: int = 0
    triage: dict[str, Any] = field(default_factory=dict)
    # Aggregate filter/verify detail for one Live Trace summary event.
    reject_reasons: dict[str, int] = field(default_factory=dict)
    accepted_companies: list[dict[str, Any]] = field(default_factory=list)
    surviving_pool: list[dict[str, Any]] = field(default_factory=list)
    # Per-source companies as returned by the provider (before hard filters).
    produced_companies: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    failed_websites: list[dict[str, Any]] = field(default_factory=list)
    known_skipped_companies: list[dict[str, Any]] = field(default_factory=list)
    # Per-source harvest request/result metadata collected during harvest.
    source_requests: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def bump(self, source: str, key: str, n: int = 1) -> None:
        s = self.by_source.setdefault(
            source,
            {
                "harvested": 0,
                "accepted": 0,
                "rejected": 0,
                "known": 0,
                "verify_failed": 0,
                "cost_usd": 0.0,
                "errors": 0,
                "raw_rows": 0,
                "fetch_limit": 0,
                "requested": 0,
                "website_checked": 0,
                "website_passed": 0,
            },
        )
        s[key] = s.get(key, 0) + n

    def bump_reason(self, reason_key: str, n: int = 1) -> None:
        self.reject_reasons[reason_key] = self.reject_reasons.get(reason_key, 0) + n

    def note_accepted(self, cand: DiscoveryCandidate, *, limit: int = 300) -> None:
        if len(self.accepted_companies) >= limit:
            return
        self.accepted_companies.append(
            {
                "company": cand.company,
                "domain": cand.domain,
                "source": cand.discovery_source,
                "state": cand.state,
                "city": cand.city,
            }
        )

    def note_failed_website(self, *, company: str, domain: str | None, reason: str, limit: int = 40) -> None:
        if len(self.failed_websites) >= limit:
            return
        self.failed_websites.append({"company": company, "domain": domain, "reason": reason})


class MultiSourceDiscoveryProvider:
    name = "multi_source_discovery"

    def __init__(
        self,
        *,
        db: Session | None,
        settings: Settings | None = None,
        harvesters: dict[str, Any] | None = None,
        verifier: WebsiteVerifier | None = None,
        source_config: dict[str, Any] | None = None,
        triage_service: DiscoveryCandidateTriageService | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        if harvesters is None and self._demo_mode():
            from acquisition.integrations.discovery.sources.demo import DemoHarvester

            harvesters = {
                "openai_web": DemoHarvester("openai_web", SOURCE_LABELS["openai_web"])
            }
            verifier = verifier or WebsiteVerifier(
                settings=self.settings.model_copy(update={"discovery_verify_websites": False})
            )
        if harvesters is None:
            client = resolve_openai_web_client(db, self.settings)
            harvesters = {
                "openai_web": OpenAIWebHarvester(client),
            }
            # Exa/NPPES exist on disk but are only registered when explicitly enabled
            # for multi_legacy rollback testing — never on the agentic path.
            if bool(getattr(self.settings, "discovery_enable_legacy_sources", False)):
                from acquisition.integrations.discovery.sources.exa import ExaCompanyHarvester
                from acquisition.integrations.discovery.sources.nppes import NppesHarvester

                harvesters["nppes"] = NppesHarvester(settings=self.settings)
                harvesters["exa"] = ExaCompanyHarvester(settings=self.settings)
        self.harvesters = harvesters or {
            "openai_web": OpenAIWebHarvester(resolve_openai_web_client(db, self.settings))
        }
        self.verifier = verifier or WebsiteVerifier(settings=self.settings)
        self._source_config = source_config
        self.triage_service = triage_service or DiscoveryCandidateTriageService(
            db, settings=self.settings
        )
        self.last_stats = RunStats()

    def _demo_mode(self) -> bool:
        from acquisition.config import is_non_production_environment

        return bool(getattr(self.settings, "discovery_demo_mode", False)) and is_non_production_environment(
            self.settings.environment
        )

    # ----- config -----

    def source_config(self) -> dict[str, Any]:
        if self._source_config is not None:
            # Explicit override (tests/callers): honor injected harvester keys.
            out: dict[str, Any] = {}
            for name in self.harvesters:
                raw = self._source_config.get(name) if isinstance(self._source_config, dict) else None
                raw = raw if isinstance(raw, dict) else {}
                default_limit = DEFAULT_FETCH_LIMITS.get(name, 10)
                out[name] = {
                    "enabled": bool(raw.get("enabled", name in SUPPORTED_SOURCES)),
                    "fetch_limit": clamp_fetch_limit(
                        name,
                        raw.get("fetch_limit", default_limit),
                        default=default_limit,
                    ),
                }
            return out
        raw = None
        if self.db is not None:
            try:
                from acquisition.services.icp_settings_service import IcpSettingsService

                raw = IcpSettingsService(self.db).get_or_create().discovery_sources
            except Exception:  # noqa: BLE001
                raw = None
        return merged_source_config(raw, self.settings)

    def active_sources(self) -> list[str]:
        cfg = self.source_config()
        return [
            name
            for name, h in self.harvesters.items()
            if cfg.get(name, {}).get("enabled")
            and int(cfg[name].get("fetch_limit") or 0) > 0
            and h.configured()
        ]

    def source_status(self) -> list[dict[str, Any]]:
        cfg = self.source_config()
        out = []
        for name, h in self.harvesters.items():
            fetch_limit = int(cfg.get(name, {}).get("fetch_limit", DEFAULT_FETCH_LIMITS[name]))
            out.append(
                {
                    "name": name,
                    "label": getattr(h, "label", SOURCE_LABELS.get(name, name)),
                    "enabled": bool(cfg.get(name, {}).get("enabled")),
                    "fetch_limit": fetch_limit,
                    "configured": bool(h.configured()),
                    "message": h.status_message(),
                }
            )
        return out

    def status(self) -> ProviderStatus:
        active = self.active_sources()
        if not active:
            return ProviderStatus(
                configured=False,
                provider_name=self.name,
                message="No discovery source is enabled and configured.",
            )
        return ProviderStatus(
            configured=True,
            provider_name=self.name,
            message="Sources: " + ", ".join(SOURCE_LABELS.get(s, s) for s in active),
        )

    # ----- discovery -----

    def search_companies(
        self,
        *,
        icp: dict[str, Any],
        limit: int = 5,
        preview: bool = False,
    ) -> list[DiscoveryCandidate]:
        """Harvest each enabled source up to its fetch_limit, triage, return top ``limit``.

        ``limit`` is the final count selected for research (or preview), NOT the
        per-source fetch budget. Fetch budgets come from Admin discovery_sources.
        When ``preview=True``, slice ledger cursors / rests and prospect
        ``times_seen`` are not mutated.
        """
        if self.db is None:
            raise DiscoveryProviderError("multi_source_requires_db", provider=self.name)
        requested = max(1, int(limit))
        global_max = global_triage_max_candidates(self.settings)
        stats = RunStats(requested=requested, pool_target=requested)
        self.last_stats = stats
        active = self.active_sources()
        if not active:
            raise DiscoveryProviderError("discovery_provider_configuration_required", provider=self.name)

        ledger = DiscoverySliceService(self.db)
        created = ledger.ensure_universe(icp, active)
        cfg = self.source_config()
        fetch_limits = {s: int(cfg[s]["fetch_limit"]) for s in active}
        for s, fl in fetch_limits.items():
            stats.bump(s, "fetch_limit", 0)
            stats.by_source[s]["fetch_limit"] = fl
        excluded = [str(x) for x in (icp.get("excluded_categories") or [])]
        priority_states = list((icp.get("geography") or {}).get("states") or [])
        buckets = buckets_from_icp(icp)
        limits_txt = ", ".join(
            f"{SOURCE_LABELS.get(s, s)}={fetch_limits[s]}" for s in active
        )
        live_trace.event(
            "plan",
            (
                f"{'Preview' if preview else 'Plan'}: requested final prospects={requested}; "
                f"source fetch limits: {limits_txt}"
            ),
            sources={s: {"fetch_limit": fetch_limits[s]} for s in active},
            categories=buckets,
            new_slices_created=created,
            priority_states=priority_states,
            geography=(
                "United States (nationwide)"
                if (icp.get("geography") or {}).get("nationwide")
                or "United States" in ((icp.get("geography") or {}).get("countries") or [])
                else None
            ),
            exclusions=excluded[:20],
            requested=requested,
            fetch_limits=fetch_limits,
            preview=preview,
            slices_planned=[],  # filled after harvest via filtering summary details
        )

        seen_keys: set[str] = set()
        pool: list[DiscoveryCandidate] = []
        used_slices: set[str] = set()
        max_slices = max(1, int(self.settings.discovery_max_slices_per_source or 6))

        for source in active:
            self._harvest_source(
                source=source,
                fetch_limit=fetch_limits[source],
                pool=pool,
                icp=icp,
                excluded=excluded,
                buckets=buckets,
                priority_states=priority_states,
                ledger=ledger,
                seen_keys=seen_keys,
                used_slices=used_slices,
                stats=stats,
                preview=preview,
                max_slices=max_slices,
            )

        # One filtering summary for the entire cheap-screen stage (all sources).
        removed = {
            "Duplicates (same run)": stats.duplicates_in_run,
            "Known recently researched": stats.known_skipped,
            "Excluded business types": int(stats.reject_reasons.get("Excluded business types", 0)),
            "Directory/listicle results": int(stats.reject_reasons.get("Directory/listicle results", 0)),
            "Invalid/unverified websites": stats.verify_failed,
            "Other": int(stats.reject_reasons.get("Other", 0)),
        }
        removed = {k: v for k, v in removed.items() if v}
        surviving_pool = [serialize_candidate_for_pool(c) for c in pool]
        stats.surviving_pool = surviving_pool
        live_trace.event(
            "filter",
            (
                f"Filtering summary: evaluated={stats.evaluated}, "
                f"remaining={len(pool)}, "
                f"removed={sum(removed.values())}"
            ),
            level="success" if pool else "warning",
            evaluated=stats.evaluated,
            remaining=len(pool),
            removed=removed,
            reject_reasons=dict(stats.reject_reasons),
            failed_websites=list(stats.failed_websites),
            known_skipped_companies=list(stats.known_skipped_companies[:40]),
            accepted_companies=list(stats.accepted_companies),
            surviving_pool=surviving_pool,
            surviving_count=len(surviving_pool),
        )

        pool_before_cap = len(pool)
        stats.pool_before_cap = pool_before_cap
        # Absolute safety ceiling only — never evidence-completeness truncation.
        if len(pool) > global_max:
            msg = (
                f"Global AI comparison safety limit exceeded: "
                f"surviving={len(pool)} > max={global_max}. "
                f"Refusing to discard candidates via evidence completeness."
            )
            logger.error(msg)
            live_trace.event(
                "triage",
                msg,
                level="error",
                surviving=len(pool),
                global_triage_max=global_max,
                sent_to_ai=0,
            )
            raise DiscoveryProviderError(
                "global_triage_max_exceeded",
                provider=self.name,
            )

        stats.pool_available = len(pool)
        live_trace.event(
            "plan",
            (
                f"Candidate pool: surviving={pool_before_cap}, "
                f"sent_to_global_ai_comparison={len(pool)}, "
                f"requested_final={requested}"
            ),
            level="success" if pool else "warning",
            pool_before_cap=pool_before_cap,
            surviving=pool_before_cap,
            sent_to_ai=len(pool),
            sent_to_global_ai_comparison=len(pool),
            requested=requested,
            global_triage_max=global_max,
            known_skipped=stats.known_skipped,
            rejected=stats.rejected,
            verify_failed=stats.verify_failed,
            by_source=stats.by_source,
            harvest_cost_usd=round(stats.harvest_cost_usd, 4),
            slices=list(stats.slices)[:40],
            # Full pool already stored on Filtering summary — keep only the count here.
            surviving_count=len(surviving_pool),
        )

        triage = self.triage_service.triage(icp=icp, candidates=pool, select_count=requested)
        stats.triage = {
            "source": triage.source,
            "model": triage.model,
            "ok": triage.ok,
            "error": triage.error,
            "requested": triage.requested,
            "pool_size": triage.pool_size,
            "considered_count": triage.considered_count,
            "selected_count": len(triage.selected),
            "selection_summary": triage.selection_summary,
            "items": [
                {
                    "candidate_id": it.candidate_id,
                    "company": it.candidate.company,
                    "domain": it.candidate.domain,
                    "source": it.candidate.discovery_source,
                    "slice_key": it.candidate.slice_key,
                    "state": it.candidate.state,
                    "triage_score": it.triage_score,
                    "rank": it.rank,
                    "reason": it.reason,
                    "selected": it.selected,
                    "positive_signals": it.positive_signals,
                    "uncertainties": it.uncertainties,
                }
                for it in triage.items
            ],
        }
        self._emit_triage_trace(
            triage,
            requested=requested,
            pool_target=len(pool),
            preview=preview,
            stats=stats,
        )
        return list(triage.selected)

    def _harvest_source(
        self,
        *,
        source: str,
        fetch_limit: int,
        pool: list[DiscoveryCandidate],
        icp: dict[str, Any],
        excluded: list[str],
        buckets: list[str],
        priority_states: list[str],
        ledger: DiscoverySliceService,
        seen_keys: set[str],
        used_slices: set[str],
        stats: RunStats,
        preview: bool,
        max_slices: int,
    ) -> None:
        """Spend the source's per-run fetch budget across slices; do not stop early on accepts."""
        if fetch_limit <= 0:
            return
        harvester = self.harvesters[source]
        remaining = int(fetch_limit)
        label = SOURCE_LABELS.get(source, source)
        picked = [
            row
            for row in ledger.pick(
                source, count=max_slices, categories=buckets, priority_states=priority_states
            )
            if row.key not in used_slices
        ]
        if not picked:
            live_trace.event(
                "harvest",
                f"{label} REQUEST — no fresh slices available",
                level="warning",
                source=source,
                fetch_limit=fetch_limit,
            )
            return

        accepted_before = int(stats.by_source.get(source, {}).get("accepted", 0) or 0)
        slice_requests: list[dict[str, Any]] = []
        harvest_error: str | None = None

        for row in picked:
            if remaining <= 0:
                break
            used_slices.add(row.key)
            spec = ledger.to_spec(row, exclude_domains=ledger.known_domains_near(row.key))
            slice_request = remaining
            try:
                if source == "openai_web":
                    harvest: HarvestResult = harvester.harvest(
                        spec, limit=slice_request, excluded=excluded
                    )
                else:
                    harvest = harvester.harvest(spec, limit=slice_request)
            except Exception as exc:  # noqa: BLE001
                logger.exception("harvest failed for %s", spec.key)
                harvest = HarvestResult(error=f"harvest_exception:{exc.__class__.__name__}")

            stats.harvest_cost_usd += harvest.cost_usd
            stats.bump(source, "cost_usd", 0)
            stats.by_source[source]["cost_usd"] = round(
                stats.by_source[source]["cost_usd"] + harvest.cost_usd, 5
            )
            stats.bump(source, "harvested", len(harvest.candidates))
            raw = int(harvest.raw_count or 0)
            stats.bump(source, "raw_rows", raw)
            stats.bump(source, "requested", slice_request)
            if harvest.error:
                stats.bump(source, "errors")
                harvest_error = harvest.error
                live_trace.event(
                    "error",
                    f"{label} ERROR — {harvest.error}",
                    level="error",
                    source=source,
                    slice=spec.key,
                    request=harvest.request,
                )

            req_meta: dict[str, Any] = {
                "slice": spec.key,
                "category": spec.category,
                "place": spec.place,
                "angle": spec.angle,
                "requested": slice_request,
                "raw_received": raw,
                "candidates_produced": len(harvest.candidates),
                "request": harvest.request,
            }
            slice_requests.append(req_meta)
            stats.source_requests.setdefault(source, []).append(req_meta)

            # Consume per-run budget from harvested company candidates.
            if not harvest.error:
                if source == "openai_web":
                    produced = max(raw, len(harvest.candidates))
                    if produced > 0:
                        remaining -= min(remaining, produced)
                elif raw > 0:
                    remaining -= min(remaining, raw)
                elif harvest.candidates:
                    remaining -= min(remaining, len(harvest.candidates))

            if not preview:
                ledger.record_harvest(
                    row,
                    candidates=len(harvest.candidates),
                    next_cursor=harvest.next_cursor,
                    exhausted=harvest.exhausted,
                    error=harvest.error,
                )
            slice_new = 0
            produced_rows = stats.produced_companies.setdefault(source, [])
            for cand in harvest.candidates:
                if len(produced_rows) < 300:
                    produced_rows.append(
                        {
                            "company": cand.company,
                            "domain": cand.domain,
                            "source": cand.discovery_source or source,
                            "state": cand.state,
                            "city": cand.city,
                        }
                    )
                decision = self._screen(
                    cand,
                    excluded=excluded,
                    seen_keys=seen_keys,
                    stats=stats,
                    preview=preview,
                )
                if decision is None:
                    continue
                pool.append(decision)
                slice_new += 1
            stats.slices.append({"key": spec.key, "source": source, "accepted": slice_new})

        accepted_after = int(stats.by_source.get(source, {}).get("accepted", 0) or 0)
        survived = accepted_after - accepted_before
        src_stats = stats.by_source.get(source, {})
        raw_total = int(src_stats.get("raw_rows", 0) or 0)
        produced_total = int(src_stats.get("harvested", 0) or 0)
        website_checked = int(src_stats.get("website_checked", 0) or 0)
        website_passed = int(src_stats.get("website_passed", 0) or 0)
        website_failed = int(src_stats.get("verify_failed", 0) or 0)
        returned_companies = list(stats.produced_companies.get(source) or [])

        # ONE request event + ONE result event per source (not per slice / candidate).
        live_trace.event(
            "harvest",
            f"{label} REQUEST — fetch_limit={fetch_limit}, slices={len(slice_requests)}",
            level="info",
            source=source,
            fetch_limit=fetch_limit,
            slices=slice_requests,
            queries=[
                {
                    "category": q.get("category"),
                    "place": q.get("place"),
                    "angle": q.get("angle"),
                    "requested": q.get("requested"),
                    "request": q.get("request"),
                }
                for q in slice_requests
            ],
        )
        live_trace.event(
            "harvest",
            (
                f"{label} RESULT — raw={raw_total}, candidates={produced_total}, "
                f"survived={survived}"
                + (f", website_checked={website_checked}, passed={website_passed}, failed={website_failed}"
                   if website_checked else "")
            ),
            level="error" if harvest_error and not survived else ("success" if survived or raw_total else "warning"),
            source=source,
            fetch_limit=fetch_limit,
            requested=int(src_stats.get("requested", 0) or 0),
            raw_received=raw_total,
            candidates_produced=produced_total,
            survived=survived,
            website_checked=website_checked,
            website_passed=website_passed,
            website_failed=website_failed,
            error=harvest_error,
            # API returns (pre-filter) for Source results UI.
            returned_companies=returned_companies,
            produced_companies=returned_companies,
            # Survivors still recorded for debugging / pool math.
            accepted_companies=[
                {
                    "company": c.company,
                    "domain": c.domain,
                    "source": c.discovery_source,
                    "state": c.state,
                    "city": c.city,
                }
                for c in pool
                if (c.discovery_source or "") == source
            ],
            failed_websites=[
                f for f in stats.failed_websites
            ][:20],
            slices=slice_requests,
        )
    def _emit_triage_trace(
        self,
        triage: Any,
        *,
        requested: int,
        pool_target: int,
        preview: bool,
        stats: RunStats,
    ) -> None:
        if triage.source == "deterministic_fallback":
            live_trace.event(
                "triage",
                f"AI triage unavailable → deterministic fallback used"
                + (f" ({triage.error})" if triage.error else ""),
                level="warning",
                model=triage.model,
                error=triage.error,
            )
        live_trace.event(
            "triage",
            (
                f"AI candidate triage: model={triage.model or triage.source}, "
                f"considered={triage.pool_size}, requested={requested}"
            ),
            level="info",
            requested=requested,
            pool_target=pool_target,
            pool_available=triage.pool_size,
            considered_count=triage.considered_count or triage.pool_size,
            sent_to_ai=triage.pool_size,
            sent_to_global_ai_comparison=triage.pool_size,
            model=triage.model,
            triage_source=triage.source,
            preview=preview,
        )
        selected_items = [
            {
                "rank": it.rank,
                "company": it.candidate.company,
                "domain": it.candidate.domain,
                "source": it.candidate.discovery_source,
                "state": it.candidate.state,
                "category": it.candidate.industry,
                "slice_key": it.candidate.slice_key,
                "triage_score": it.triage_score,
                "selection_score": it.triage_score,
                "reason": it.reason,
                "positive_signals": it.positive_signals,
                "evidence": it.positive_signals,
                "uncertainties": it.uncertainties,
                "selected": True,
                "candidate_id": it.candidate_id,
            }
            for it in triage.items
            if it.selected
        ]
        # Compact unselected names only (no per-candidate scores) — one structured blob.
        selected_keys = {
            (
                (it.candidate.discovery_source or ""),
                (it.candidate.company or "").lower(),
                (it.candidate.domain or ""),
            )
            for it in triage.items
            if it.selected
        }
        not_selected_items = []
        for row in stats.surviving_pool or []:
            key = (
                (row.get("discovery_source") or row.get("source") or ""),
                (row.get("company") or "").lower(),
                (row.get("domain") or ""),
            )
            if key in selected_keys:
                continue
            not_selected_items.append(
                {
                    "company": row.get("company"),
                    "domain": row.get("domain"),
                    "source": row.get("discovery_source") or row.get("source"),
                    "state": row.get("state"),
                    "selected": False,
                }
            )
        selected_lines = "; ".join(
            f"{x['company']} ({x['triage_score']:g})" for x in selected_items[:8]
        ) or "none"
        live_trace.event(
            "triage",
            (
                f"AI triage result: considered={triage.pool_size}, "
                f"selected={len(selected_items)}, not_selected={len(not_selected_items)}"
                + (f" — {selected_lines}" if selected_items else "")
            ),
            level="success" if selected_items else "warning",
            requested=requested,
            pool_target=pool_target,
            pool_available=triage.pool_size,
            considered_count=triage.considered_count or triage.pool_size,
            sent_to_ai=triage.pool_size,
            sent_to_global_ai_comparison=triage.pool_size,
            model=triage.model,
            triage_source=triage.source,
            selected=selected_items,
            not_selected=not_selected_items,
            selection_summary=triage.selection_summary,
            preview=preview,
        )
        if preview:
            by_source = {
                k: {
                    "raw": int(v.get("raw_rows", 0) or 0),
                    "candidates": int(v.get("harvested", 0) or 0),
                    "survived": int(v.get("accepted", 0) or 0),
                }
                for k, v in (stats.by_source or {}).items()
            }
            live_trace.event(
                "run",
                (
                    "Preview complete — no prospects saved, researched, scored, or contacted. "
                    f"Surviving={stats.pool_before_cap}, "
                    f"sent_to_global_ai_comparison={triage.pool_size}, "
                    f"selected={len(selected_items)}"
                ),
                level="success",
                selected=selected_items,
                by_source=by_source,
                pool_before_cap=stats.pool_before_cap,
                sent_to_ai=triage.pool_size,
                sent_to_global_ai_comparison=triage.pool_size,
                selected_count=len(selected_items),
                preview_stopped=True,
            )

    def _screen(
        self,
        cand: DiscoveryCandidate,
        *,
        excluded: list[str],
        seen_keys: set[str],
        stats: RunStats,
        preview: bool = False,
    ) -> DiscoveryCandidate | None:
        source = cand.discovery_source or "unknown"
        name = cand.company
        stats.evaluated += 1
        if cand.domain:
            cand.domain = root_domain(cand.domain) or cand.domain

        def reject(reason: str, *, counter: str = "rejected", reason_key: str = "Other") -> None:
            if counter == "rejected":
                stats.rejected += 1
            elif counter == "verify_failed":
                stats.verify_failed += 1
            stats.bump(source, counter)
            stats.bump_reason(reason_key)
            logger.debug("screen reject %s (%s): %s", name, source, reason)

        if cand.domain and is_non_company_domain(cand.domain):
            reject("directory / review / social / news domain", reason_key="Directory/listicle results")
            return None
        if looks_like_listicle(name):
            reject("list or review page title, not a company", reason_key="Directory/listicle results")
            return None
        hit = _exclusion_hit(excluded, name, cand.industry)
        if hit:
            reject(f"excluded business type ({hit})", reason_key="Excluded business types")
            return None

        key = f"npi:{cand.npi}" if cand.npi else (f"d:{cand.domain}" if cand.domain else f"n:{normalize_company_name(name)}")
        if key in seen_keys:
            stats.duplicates_in_run += 1
            stats.bump_reason("Duplicates (same run)")
            return None
        seen_keys.add(key)

        existing = self._find_existing(cand)
        if existing is not None:
            refresh_days = max(1, int(self.settings.discovery_refresh_days or 30))
            anchor = existing.last_researched_at or existing.created_at
            if anchor is not None and anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            fresh = anchor is not None and datetime.now(timezone.utc) - anchor < timedelta(days=refresh_days)
            if not preview:
                self._mark_seen(existing)
            if fresh or existing.acquisition_status == AcquisitionStatus.EXCLUDED:
                stats.known_skipped += 1
                stats.bump(source, "known")
                if len(stats.known_skipped_companies) < 40:
                    stats.known_skipped_companies.append(
                        {
                            "company": existing.company,
                            "domain": existing.domain,
                            "researched": anchor.date().isoformat() if anchor else None,
                        }
                    )
                logger.debug("screen known skip %s", existing.company)
                return None
            cand.raw["refresh_of"] = str(existing.id)
            logger.debug("screen refresh due %s", existing.company)

        if cand.domain and self.verifier.enabled:
            stats.bump(source, "website_checked")
            v = self.verifier.verify(cand.domain, name)
            if not v.ok:
                stats.note_failed_website(company=name, domain=cand.domain, reason=v.reason or "failed")
                reject(
                    f"website check failed — {v.reason}",
                    counter="verify_failed",
                    reason_key="Invalid/unverified websites",
                )
                return None
            if v.final_domain and v.final_domain != cand.domain:
                cand.domain = v.final_domain
                cand.website = f"https://{v.final_domain}"
                if self._find_existing(cand) is not None and "refresh_of" not in cand.raw:
                    stats.known_skipped += 1
                    stats.bump(source, "known")
                    logger.debug("screen known after redirect %s", name)
                    return None
            cand.raw["website_verified"] = True
            cand.raw["verify_title"] = v.title
            if v.name_match is False:
                cand.identity_uncertain = True
            stats.bump(source, "website_passed")

        stats.bump(source, "accepted")
        stats.note_accepted(cand)
        return cand

    def _find_existing(self, cand: DiscoveryCandidate) -> Prospect | None:
        assert self.db is not None
        if cand.npi:
            row = self.db.scalar(select(Prospect).where(Prospect.npi == cand.npi).limit(1))
            if row is not None:
                return row
        return find_existing_company_or_prospect(
            self.db,
            website=cand.website,
            domain=root_domain(cand.domain) if cand.domain else None,
            company=cand.company,
            executive_name=None,
        )

    def _mark_seen(self, prospect: Prospect) -> None:
        assert self.db is not None
        prospect.last_seen_at = datetime.now(timezone.utc)
        prospect.times_seen = int(prospect.times_seen or 0) + 1
        self.db.add(prospect)
        self.db.commit()

    def search_company_context(self, **_: Any) -> list[DiscoveryCandidate]:
        return []
