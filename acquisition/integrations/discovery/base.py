"""Provider interfaces for autonomous discovery and public research."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from acquisition.integrations.discovery.types import (
    DiscoveryCandidate,
    ProviderStatus,
    PublicResearchResult,
)


@runtime_checkable
class DiscoveryProvider(Protocol):
    """Search for company candidates from permitted public sources."""

    name: str

    def status(self) -> ProviderStatus: ...

    def search_companies(
        self,
        *,
        icp: dict[str, Any],
        limit: int = 5,
    ) -> list[DiscoveryCandidate]: ...

    def search_company_context(
        self,
        *,
        company: str,
        domain: str | None = None,
        website: str | None = None,
        icp: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]: ...


@runtime_checkable
class ResearchProvider(Protocol):
    """Fetch structured public context for a known company/prospect."""

    name: str

    def status(self) -> ProviderStatus: ...

    def fetch_public_context(
        self,
        *,
        company: str,
        website: str | None = None,
        domain: str | None = None,
        industry: str | None = None,
        icp: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> PublicResearchResult: ...


class DiscoveryProviderError(Exception):
    """Provider failed in a non-fatal way — caller should continue with partial data."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        self.provider = provider
        super().__init__(message)


class DiscoveryProviderNotConfigured(DiscoveryProviderError):
    """No usable discovery/research provider credentials or runtime."""
