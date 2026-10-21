"""Resolve discovery/research providers from settings (no invented credentials)."""

from __future__ import annotations

from acquisition.config import Settings, get_settings
from acquisition.integrations.discovery.base import DiscoveryProvider, ResearchProvider
from acquisition.integrations.discovery.mock import (
    MockDiscoveryProvider,
    MockResearchProvider,
    NullDiscoveryProvider,
    NullResearchProvider,
)
from acquisition.integrations.discovery.web_search import (
    WebSearchDiscoveryProvider,
    WebSearchResearchProvider,
)

_LIVE_ALIASES = frozenset(
    {
        "live",
        "web",
        "web_search",
        "websearch",
        "serper",
        "brave",
        "brave_search",
        "public_search",
    }
)
_MOCK_ALIASES = frozenset({"mock", "fixture", "test"})
_AGENTIC_ALIASES = frozenset({"openai_agentic", "agentic", "multi", "multi_source", "auto", "v2", "v3"})
_LEGACY_MULTI_ALIASES = frozenset({"multi_legacy", "legacy_multi", "v2_legacy"})


def is_agentic_discovery_provider_name(name: str | None) -> bool:
    return (name or "").strip().lower() in _AGENTIC_ALIASES


def is_legacy_multi_discovery_provider_name(name: str | None) -> bool:
    return (name or "").strip().lower() in _LEGACY_MULTI_ALIASES


def is_multi_discovery_provider_name(name: str | None) -> bool:
    """True for any multi-style provider (agentic primary or legacy multi)."""
    return is_agentic_discovery_provider_name(name) or is_legacy_multi_discovery_provider_name(name)


def is_live_discovery_provider_name(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return n in _LIVE_ALIASES or is_multi_discovery_provider_name(n)


def is_mock_discovery_provider_name(name: str | None) -> bool:
    return (name or "").strip().lower() in _MOCK_ALIASES


def resolve_discovery_provider(settings: Settings | None = None, db=None) -> DiscoveryProvider:
    cfg = settings or get_settings()
    name = (cfg.discovery_provider or "none").strip().lower()
    if is_agentic_discovery_provider_name(name):
        from acquisition.integrations.discovery.agentic_provider import AgenticDiscoveryProvider

        return AgenticDiscoveryProvider(db=db, settings=cfg)
    if is_legacy_multi_discovery_provider_name(name):
        from acquisition.integrations.discovery.multi_source import MultiSourceDiscoveryProvider

        return MultiSourceDiscoveryProvider(db=db, settings=cfg)
    if is_mock_discovery_provider_name(name):
        return MockDiscoveryProvider()
    if is_live_discovery_provider_name(name):
        return WebSearchDiscoveryProvider(settings=cfg)
    return NullDiscoveryProvider()


def resolve_research_provider(settings: Settings | None = None, db=None) -> ResearchProvider:
    cfg = settings or get_settings()
    name = (cfg.discovery_provider or "none").strip().lower()
    choice = (getattr(cfg, "research_provider", "auto") or "auto").strip().lower()
    from acquisition.config import is_non_production_environment

    if (
        getattr(cfg, "discovery_demo_mode", False)
        and is_non_production_environment(cfg.environment)
        and is_multi_discovery_provider_name(name)
    ):
        from acquisition.integrations.discovery.sources.demo import DemoResearchProvider

        return DemoResearchProvider()
    if choice in {"openai_web", "openai"} or (choice == "auto" and is_multi_discovery_provider_name(name)):
        from acquisition.integrations.discovery.multi_source import resolve_openai_web_client
        from acquisition.integrations.discovery.sources.openai_research import OpenAIWebResearchProvider

        client = resolve_openai_web_client(db, cfg)
        if client.configured or choice != "auto":
            return OpenAIWebResearchProvider(client)
        if (cfg.discovery_search_api_key or "").strip():
            return WebSearchResearchProvider(settings=cfg)
        return OpenAIWebResearchProvider(client)
    if choice in {"web_search", "serper", "brave"}:
        return WebSearchResearchProvider(settings=cfg)
    if choice == "mock":
        return MockResearchProvider()
    if is_mock_discovery_provider_name(name):
        return MockResearchProvider()
    if is_live_discovery_provider_name(name):
        return WebSearchResearchProvider(settings=cfg)
    return NullResearchProvider()
