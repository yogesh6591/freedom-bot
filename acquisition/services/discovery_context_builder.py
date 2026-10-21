"""Build structured previous-discovery context for agentic discovery prompts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.prospect import AcquisitionStatus, ApprovalStatus, Prospect
from acquisition.services.domain_tools import root_domain
from acquisition.services.prospect_identity import normalize_company_name


def _category_key(prospect: Prospect) -> str:
    raw = (prospect.business_type or prospect.industry or "uncategorized").strip().lower()
    return raw or "uncategorized"


def _domain_of(prospect: Prospect) -> str | None:
    raw = prospect.domain or prospect.website
    if not raw:
        return None
    domain = root_domain(raw) if raw else None
    return (domain or str(raw)).strip().lower() or None


class DiscoveryContextBuilder:
    """Summarize prior prospects for the next agentic discovery run.

    Database remains authoritative for deduplication — this context is guidance
    only (coverage bias + do-not-rediscover lists), not a full domain dump.
    """

    def __init__(self, db: Session, *, recent_limit: int = 40) -> None:
        self.db = db
        self.recent_limit = max(5, min(int(recent_limit), 80))

    def build(self, icp: dict[str, Any] | None = None) -> dict[str, Any]:
        del icp  # reserved for future ICP-scoped filtering
        rows = list(
            self.db.scalars(
                select(Prospect)
                .where(Prospect.discovery_source.is_not(None))
                .order_by(Prospect.first_seen_at.desc().nullslast(), Prospect.created_at.desc())
                .limit(400)
            )
        )
        # Broader domain scan for rediscovery avoidance (may include non-AI prospects).
        domain_rows = list(
            self.db.scalars(
                select(Prospect)
                .where(Prospect.domain.is_not(None) | Prospect.website.is_not(None))
                .order_by(Prospect.updated_at.desc().nullslast(), Prospect.created_at.desc())
                .limit(800)
            )
        )
        category_coverage: Counter[str] = Counter()
        state_coverage: Counter[str] = Counter()
        missing_fields: Counter[str] = Counter()
        previous: list[dict[str, Any]] = []
        known_domains: list[str] = []
        in_progress: list[str] = []
        opt_out_excluded: list[str] = []
        seen_domains: set[str] = set()

        active_statuses = {
            AcquisitionStatus.READY_FOR_OUTREACH,
            AcquisitionStatus.READY_FOR_SCORING,
            AcquisitionStatus.SCORED,
            AcquisitionStatus.RESEARCHED,
            AcquisitionStatus.DISCOVERED,
        }
        blocked_statuses = {
            AcquisitionStatus.EXCLUDED,
            AcquisitionStatus.UNREACHABLE,
        }

        for p in domain_rows:
            domain = _domain_of(p)
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            known_domains.append(domain)
            status = p.acquisition_status
            approval = p.approval_status
            if status in active_statuses or approval == ApprovalStatus.APPROVED:
                if len(in_progress) < 120:
                    in_progress.append(domain)
            if status in blocked_statuses or approval == ApprovalStatus.REJECTED:
                if len(opt_out_excluded) < 120:
                    opt_out_excluded.append(domain)

        for p in rows:
            category_coverage[_category_key(p)] += 1
            if p.state:
                state_coverage[str(p.state).upper()] += 1
            for field, present in (
                ("website", bool(p.website or p.domain)),
                (
                    "decision_maker",
                    bool((p.executive_name or "").strip())
                    and (p.executive_name or "").lower() != "unknown decision-maker",
                ),
                ("email", bool((p.email or "").strip()) and "example.com" not in (p.email or "").lower()),
                ("phone", bool(p.company_phone)),
                ("revenue", bool(p.revenue_band)),
                ("headcount", bool(p.headcount_band)),
            ):
                if not present:
                    missing_fields[field] += 1

        for p in rows[: self.recent_limit]:
            domain = _domain_of(p)
            previous.append(
                {
                    "company_name": p.company,
                    "normalized_domain": domain,
                    "category": p.business_type or p.industry,
                    "state": p.state,
                    "city": p.city,
                    "date_discovered": (p.first_seen_at or p.discovered_at or p.created_at).isoformat()
                    if (p.first_seen_at or p.discovered_at or p.created_at)
                    else None,
                    "fields_found": {
                        "website": bool(p.website or p.domain),
                        "decision_maker": bool(p.executive_name),
                        "email": bool(p.email) and "example.com" not in (p.email or "").lower(),
                        "phone": bool(p.company_phone),
                        "revenue": bool(p.revenue_band),
                        "headcount": bool(p.headcount_band),
                    },
                    "acquisition_status": getattr(p.acquisition_status, "value", p.acquisition_status),
                    "discovery_source": p.discovery_source,
                }
            )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_discovered_prospects": len(rows),
            "category_coverage": dict(category_coverage.most_common(30)),
            "state_coverage": dict(state_coverage.most_common(60)),
            "commonly_missing_fields": dict(missing_fields.most_common(12)),
            "previous_prospects": previous,
            "known_domains": known_domains[:300],
            "do_not_rediscover_domains": known_domains[:300],
            "in_progress_or_active_domains": in_progress[:120],
            "opt_out_or_excluded_domains": opt_out_excluded[:120],
            "guidance": (
                "Use previous discovery history as context. If one valid ICP category has been "
                "heavily covered and another under-covered, explore the under-covered area more "
                "actively. Do NOT select a weaker company merely to balance categories. "
                "Overall ICP quality remains more important than artificial quotas. "
                "Do not rediscover domains listed as known/in-progress/excluded when ineligible. "
                "Database remains authoritative for final deduplication."
            ),
        }

    @staticmethod
    def company_keys_for_prompt(context: dict[str, Any], *, max_names: int = 40) -> list[str]:
        names: list[str] = []
        for row in context.get("previous_prospects") or []:
            name = normalize_company_name(str(row.get("company_name") or ""))
            if name:
                names.append(name)
            if len(names) >= max_names:
                break
        return names
