"""
Unit tests for Search Providers, Registry, Circuit Breaker, Caching, and Orchestrator.
"""

from __future__ import annotations

import pytest
import time
from unittest.mock import MagicMock, AsyncMock

from searchops.search.aggregator import FederatedSearchAggregator
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability, SearchProfile
from searchops.search.registry import SearchProviderRegistry
from searchops.search.health import CircuitBreaker, CircuitState, SearchHealthMonitor
from searchops.search.cache import canonicalize_query
from searchops.search.orchestrator import SearchOrchestrator


class DummySearchProvider(ISearchProvider):
    def __init__(self, name: str, items: list[SearchResultItem]) -> None:
        self._name = name
        self._items = items

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> set[SearchCapability]:
        return {SearchCapability.SEMANTIC}

    @property
    def cost_per_query(self) -> float:
        return 0.001

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        return self._items


@pytest.mark.unit
@pytest.mark.asyncio
async def test_federated_search_aggregator_deduplication():
    p1_items = [
        SearchResultItem(title="Item 1", url="https://example.com/1", snippet="s1", provider="p1", score=0.9),
        SearchResultItem(title="Item 2 Duplicate", url="https://example.com/2", snippet="s2", provider="p1", score=0.8),
    ]
    p2_items = [
        SearchResultItem(title="Item 2 Duplicate Diff Case", url="https://EXAMPLE.com/2/", snippet="s2", provider="p2", score=0.95),
        SearchResultItem(title="Item 3", url="https://example.com/3", snippet="s3", provider="p2", score=0.7),
    ]

    provider1 = DummySearchProvider("p1", p1_items)
    provider2 = DummySearchProvider("p2", p2_items)

    aggregator = FederatedSearchAggregator()
    
    # Manually test fusion logic directly
    fused = aggregator.fuse_results([p1_items, p2_items])
    assert len(fused) == 3
    urls = [r.url.lower().rstrip("/") for r in fused]
    assert urls == ["https://example.com/2", "https://example.com/1", "https://example.com/3"]


@pytest.mark.unit
def test_query_canonicalization():
    raw_query = "  What, is LangGraph?  "
    canonical = canonicalize_query(raw_query)
    assert canonical == "what is langgraph"


@pytest.mark.unit
def test_circuit_breaker_transitions():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_sec=0.1)
    
    assert breaker.can_execute() is True
    assert breaker.state == CircuitState.CLOSED

    # Record first failure
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    
    # Record second failure -> trips circuit to OPEN
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False

    # Wait for recovery timeout to pass
    time.sleep(0.12)
    assert breaker.can_execute() is True
    assert breaker.state == CircuitState.HALF_OPEN

    # Record success -> transitions back to CLOSED
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.unit
def test_provider_registry_resolution():
    registry_inst = SearchProviderRegistry()
    p1 = DummySearchProvider("p1", [])
    
    registry_inst.register(p1, priority=10, enabled=True)
    
    # Resolve capability
    res = registry_inst.resolve_by_capabilities({SearchCapability.SEMANTIC})
    assert len(res) == 1
    assert res[0].name == "p1"

    # Try resolving unsupported capability
    res_unsupported = registry_inst.resolve_by_capabilities({SearchCapability.JAVASCRIPT})
    assert len(res_unsupported) == 0
