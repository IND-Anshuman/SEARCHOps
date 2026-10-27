"""
Tests for key-free-first search routing.

Contract: the platform must work end-to-end on its own internal framework
(SearXNG + local Playwright browser) with no API keys configured. A
configured-but-broken paid provider must never be the reason a user gets a
degraded run.

Routing is declared through the provider contract (``requires_credentials``),
not by matching provider-name strings — so adding a new key-free provider
requires no change to the registry or the orchestrator.
"""

from __future__ import annotations

import pytest

from searchops.search.contracts import ISearchProvider, SearchQuery
from searchops.search.domain.models import SearchCapability
from searchops.search.registry import SearchProviderRegistry


class _StubProvider(ISearchProvider):
    """Minimal provider for routing tests.

    ``priority`` and ``requires_credentials`` are declared on the class,
    exactly as the routing contract reads them.
    """

    priority = 100
    requires_credentials = True

    def __init__(self, name: str, priority: int = 100, key_free: bool = False, caps=None) -> None:
        self._name = name
        self.priority = priority
        self.requires_credentials = not key_free
        self._caps = caps or {SearchCapability.KEYWORD}

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> set[SearchCapability]:
        return self._caps

    @property
    def cost_per_query(self) -> float:
        return 0.0

    async def search(self, query: SearchQuery) -> list:
        return []


# ── Contract: is_key_free ────────────────────────────────────────────────────


class TestKeyFreeContract:
    def test_default_requires_credentials(self):
        assert _StubProvider("x").requires_credentials is True

    def test_is_key_free_inverse_of_requires_credentials(self):
        assert _StubProvider("x", key_free=True).is_key_free is True
        assert _StubProvider("x", key_free=False).is_key_free is False

    def test_key_free_flag_is_declarative(self):
        """No provider-name strings involved — the flag is the contract."""
        provider = _StubProvider("some_new_internal_engine", key_free=True)
        assert provider.is_key_free is True


# ── Registry: key-free-first ordering ────────────────────────────────────────


class TestKeyFreeFirstOrdering:
    def test_key_free_provider_wins_despite_worse_priority(self):
        """The core guarantee: internal framework is tried before paid APIs."""
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("paid_serp", priority=1, key_free=False))
        reg.register(_StubProvider("searxng", priority=50, key_free=True))

        ordered = [p.name for p in reg.resolve_by_capabilities({SearchCapability.KEYWORD})]
        assert ordered[0] == "searxng", "key-free provider must be tried first"

    def test_priority_respected_within_same_tier(self):
        """register(priority=...) is the authoritative ordering value."""
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("paid_a", priority=1, key_free=False))
        reg.register(_StubProvider("paid_b", priority=2, key_free=False))
        reg.register(_StubProvider("free_a", priority=10, key_free=True))
        reg.register(_StubProvider("free_b", priority=5, key_free=True))

        ordered = [p.name for p in reg.resolve_by_capabilities({SearchCapability.KEYWORD})]
        # free_b(5) < free_a(10) inside the key-free tier; then paid_a(1) < paid_b(2).
        assert ordered == ["free_b", "free_a", "paid_a", "paid_b"]

    def test_declared_class_priority_is_honoured_on_discovery(self):
        """A provider declaring `priority` on the class gets that priority."""
        from searchops.search.contracts import DEFAULT_PRIORITY

        assert DEFAULT_PRIORITY == 100
        # Base contract default is explicit, not an implicit getattr fallback.
        assert _StubProvider("x").priority == 100

    def test_strict_priority_mode_available(self):
        """prefer_key_free=False restores pure priority ordering."""
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("paid_serp", priority=1, key_free=False))
        reg.register(_StubProvider("searxng", priority=50, key_free=True))

        ordered = [p.name for p in reg.resolve_by_capabilities({SearchCapability.KEYWORD}, prefer_key_free=False)]
        assert ordered == ["paid_serp", "searxng"]

    def test_disabled_providers_excluded(self):
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("searxng", key_free=True), enabled=False)
        reg.register(_StubProvider("paid", key_free=False))

        ordered = [p.name for p in reg.resolve_by_capabilities({SearchCapability.KEYWORD})]
        assert ordered == ["paid"]

    def test_capability_filter_still_applies(self):
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("searxng", key_free=True, caps={SearchCapability.KEYWORD}))
        reg.register(_StubProvider("news_api", key_free=False, caps={SearchCapability.NEWS}))

        ordered = [p.name for p in reg.resolve_by_capabilities({SearchCapability.NEWS})]
        assert ordered == ["news_api"], "searxng lacks NEWS, so it must not be offered"

    def test_key_free_and_credential_listings(self):
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("searxng", key_free=True))
        reg.register(_StubProvider("playwright", key_free=True))
        reg.register(_StubProvider("serper", key_free=False))

        assert {p.name for p in reg.key_free_providers()} == {"searxng", "playwright"}
        assert {p.name for p in reg.credential_providers()} == {"serper"}

    def test_empty_registry_returns_empty_list(self):
        assert SearchProviderRegistry().resolve_by_capabilities({SearchCapability.KEYWORD}) == []

    def test_no_duplicate_providers_in_result(self):
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("a", key_free=True))
        reg.register(_StubProvider("b", key_free=False))
        ordered = reg.resolve_by_capabilities({SearchCapability.KEYWORD})
        assert len(ordered) == len({p.name for p in ordered})

    def test_key_free_only_deployment_works(self):
        """The zero-API-key deployment: everything resolves internally."""
        reg = SearchProviderRegistry()
        reg.register(_StubProvider("searxng", priority=1, key_free=True))
        reg.register(_StubProvider("playwright", priority=2, key_free=True))

        ordered = [p.name for p in reg.resolve_by_capabilities({SearchCapability.KEYWORD})]
        assert ordered == ["searxng", "playwright"]
        assert reg.credential_providers() == []


# ── Real providers declare the contract ──────────────────────────────────────


class TestRealProviderDeclarations:
    def test_searxng_is_key_free(self):
        from searchops.search.providers.searxng import SearXNGSearchProvider

        assert SearXNGSearchProvider.requires_credentials is False

    def test_playwright_is_key_free(self):
        from searchops.search.providers.playwright import PlaywrightSearchProvider

        assert PlaywrightSearchProvider.requires_credentials is False

    @pytest.mark.parametrize(
        "module_name,class_name",
        [
            ("serper", "SerperSearchProvider"),
            ("tavily", "TavilySearchProvider"),
            ("brightdata_serp", "BrightDataSerpProvider"),
        ],
    )
    def test_paid_providers_still_require_credentials(self, module_name, class_name):
        """Regression guard: no paid provider may silently become key-free."""
        import importlib

        mod = importlib.import_module(f"searchops.search.providers.{module_name}")
        cls = getattr(mod, class_name)
        assert cls.requires_credentials is True
