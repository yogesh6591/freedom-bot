"""Public discovery / research provider package."""

from acquisition.integrations.discovery.base import (
    DiscoveryProvider,
    DiscoveryProviderError,
    DiscoveryProviderNotConfigured,
    ResearchProvider,
)
from acquisition.integrations.discovery.factory import (
    resolve_discovery_provider,
    resolve_research_provider,
)
from acquisition.integrations.discovery.types import (
    ContactPathInfo,
    DecisionMakerInfo,
    DiscoveryCandidate,
    EvidenceItem,
    PublicResearchResult,
    PublicSignal,
)
from acquisition.integrations.discovery.web_search import (
    WebSearchClient,
    WebSearchDiscoveryProvider,
    WebSearchResearchProvider,
)

__all__ = [
    "ContactPathInfo",
    "DecisionMakerInfo",
    "DiscoveryCandidate",
    "DiscoveryProvider",
    "DiscoveryProviderError",
    "DiscoveryProviderNotConfigured",
    "EvidenceItem",
    "PublicResearchResult",
    "PublicSignal",
    "ResearchProvider",
    "WebSearchClient",
    "WebSearchDiscoveryProvider",
    "WebSearchResearchProvider",
    "resolve_discovery_provider",
    "resolve_research_provider",
]
