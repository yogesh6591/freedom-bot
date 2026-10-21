"""DiscoveryProvider adapter for the agentic OpenAI discovery path."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.types import DiscoveryCandidate, ProviderStatus
from acquisition.services.agentic_discovery_service import AgenticDiscoveryService


class AgenticDiscoveryProvider:
    """Factory-facing provider. Full cycles should call AgenticDiscoveryService.run_cycle."""

    name = "openai_agentic_discovery"

    def __init__(self, *, db: Session | None, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._service: AgenticDiscoveryService | None = None
        self.last_cycle: dict[str, Any] | None = None

    def _svc(self) -> AgenticDiscoveryService:
        if self.db is None:
            raise RuntimeError("AgenticDiscoveryProvider requires a database session")
        if self._service is None:
            self._service = AgenticDiscoveryService(self.db, settings=self.settings)
        return self._service

    def status(self) -> ProviderStatus:
        st = self._svc().status() if self.db is not None else {
            "configured": False,
            "message": "database session required",
        }
        return ProviderStatus(
            configured=bool(st.get("configured")),
            provider_name=self.name,
            message=str(st.get("message") or ""),
        )

    def search_companies(self, *, icp: dict[str, Any], limit: int = 5) -> list[DiscoveryCandidate]:
        """Preview/search entry: runs agentic cycle in preview mode and maps candidates.

        Persistence is owned by run_cycle via orchestration for full cycles.
        """
        del icp
        result = self._svc().run_cycle(limit=limit, preview=True)
        self.last_cycle = result
        out: list[DiscoveryCandidate] = []
        for row in result.get("returned_prospects") or []:
            out.append(
                DiscoveryCandidate(
                    company=row["company_name"],
                    website=row.get("website"),
                    domain=row.get("normalized_domain"),
                    city=(row.get("location") or {}).get("city"),
                    state=(row.get("location") or {}).get("state"),
                    industry=row.get("industry"),
                    description=row.get("description"),
                    source="autonomous_discovery",
                    discovery_source="openai_agentic",
                    executive_name=(row.get("decision_maker") or {}).get("name"),
                    executive_title=(row.get("decision_maker") or {}).get("title"),
                    company_phone=(row.get("contact") or {}).get("phone"),
                    raw={"agentic": row, "already_researched": True},
                )
            )
        return out

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

    def source_status(self) -> list[dict[str, Any]]:
        st = self.status()
        return [
            {
                "name": "openai_agentic",
                "label": "OpenAI agentic web search",
                "enabled": True,
                "configured": st.configured,
                "message": st.message,
                "fetch_limit": int(
                    (self.settings.discovery_cycle_limit if hasattr(self.settings, "discovery_cycle_limit") else 5)
                    or 5
                ),
            }
        ]

    def run_cycle(self, *, limit: int | None = None, preview: bool = False) -> dict[str, Any]:
        result = self._svc().run_cycle(limit=limit, preview=preview)
        self.last_cycle = result
        return result
