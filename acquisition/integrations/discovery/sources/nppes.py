"""NPPES registry harvester (LEGACY — disabled in the active discovery path).

Kept for rollback / future testing when DISCOVERY_ENABLE_LEGACY_SOURCES=true
and DISCOVERY_PROVIDER=multi_legacy. The agentic OpenAI path never imports this.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.sources.base import HarvestResult, SliceSpec, state_code
from acquisition.integrations.discovery.types import DiscoveryCandidate

logger = logging.getLogger(__name__)

NPPES_API = "https://npiregistry.cms.hhs.gov/api/"
BEHAVIORAL_TAXONOMIES = (
    "Psychologist",
    "Counselor",
    "Social Worker",
    "Marriage & Family Therapist",
    "Psychiatry & Neurology",
    "Behavioral Health & Social Service Providers",
)

_GROUP_WORDS = re.compile(
    r"\b(group|associates|partners|clinic|center|practice|services|llc|llp|pc|pllc|inc)\b",
    re.I,
)


def is_behavioral_category(category: str | None) -> bool:
    text = (category or "").lower()
    return any(
        k in text
        for k in ("psycholog", "behavioral", "behavioural", "mental", "counsel", "therap", "group practice")
    )


def looks_like_group_practice(name: str, *, locations: int = 1) -> bool:
    if locations >= 3:
        return True
    return bool(_GROUP_WORDS.search(name or ""))


class NppesHarvester:
    name = "nppes"
    label = "NPPES provider registry"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport

    def configured(self) -> bool:
        return True

    def status_message(self) -> str:
        return "NPPES public registry (legacy; disabled in active discovery)"

    def supports(self, category: str) -> bool:
        return is_behavioral_category(category)

    def harvest(self, spec: SliceSpec, *, limit: int = 50) -> HarvestResult:
        taxonomy = (spec.angle or "Psychologist").strip() or "Psychologist"
        st = state_code(spec.state) or "TX"
        params = {
            "version": "2.1",
            "enumeration_type": "NPI-2",
            "taxonomy_description": taxonomy,
            "state": st,
            "limit": max(1, min(int(limit), 200)),
            "skip": max(0, int(spec.cursor or 0)),
        }
        request = {"url": NPPES_API, "params": dict(params)}
        try:
            with httpx.Client(timeout=30.0, transport=self._transport) as client:
                resp = client.get(NPPES_API, params=params)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("nppes harvest failed: %s", exc)
            return HarvestResult(error=f"nppes_error:{exc.__class__.__name__}", request=request)

        results = list(payload.get("results") or [])
        candidates: list[DiscoveryCandidate] = []
        for row in results:
            cand = self._to_candidate(row, spec=spec, taxonomy=taxonomy)
            if cand is not None:
                candidates.append(cand)
            if len(candidates) >= limit:
                break

        next_skip = int(spec.cursor or 0) + len(results)
        exhausted = len(results) < int(params["limit"])
        return HarvestResult(
            candidates=candidates[:limit],
            raw_count=len(results),
            next_cursor=None if exhausted else next_skip,
            exhausted=exhausted,
            request=request,
        )

    def _to_candidate(
        self, row: dict[str, Any], *, spec: SliceSpec, taxonomy: str
    ) -> DiscoveryCandidate | None:
        basic = row.get("basic") or {}
        name = (
            basic.get("organization_name")
            or basic.get("name")
            or " ".join(
                p
                for p in [
                    basic.get("first_name"),
                    basic.get("middle_name"),
                    basic.get("last_name"),
                ]
                if p
            )
        ).strip()
        if not name:
            return None
        locations = row.get("addresses") or []
        if not looks_like_group_practice(name, locations=len(locations)):
            return None
        npi = str(row.get("number") or "").strip() or None
        addr = next((a for a in locations if (a.get("address_purpose") or "").upper() == "LOCATION"), None)
        addr = addr or (locations[0] if locations else {})
        city = (addr.get("city") or "").strip() or None
        state = state_code(addr.get("state")) or state_code(spec.state)
        phone = (addr.get("telephone_number") or basic.get("telephone_number") or "").strip() or None
        official = row.get("taxonomies") and (row.get("basic") or {})
        exec_name = None
        exec_title = None
        ao = basic.get("authorized_official_first_name")
        if ao:
            exec_name = " ".join(
                p
                for p in [
                    basic.get("authorized_official_first_name"),
                    basic.get("authorized_official_middle_name"),
                    basic.get("authorized_official_last_name"),
                ]
                if p
            ).strip()
            exec_title = (basic.get("authorized_official_title_or_position") or "").strip() or None
        return DiscoveryCandidate(
            company=name,
            website=None,
            domain=None,
            city=city,
            state=state,
            industry=f"Behavioral health — {taxonomy}",
            source="autonomous_discovery",
            source_url=f"https://npiregistry.cms.hhs.gov/provider-view/{npi}" if npi else None,
            source_type="registry",
            discovery_source="nppes",
            slice_key=spec.key,
            npi=npi,
            executive_name=exec_name,
            executive_title=exec_title,
            company_phone=phone,
            raw={"nppes": row} if official is not None else {"nppes": row},
        )
