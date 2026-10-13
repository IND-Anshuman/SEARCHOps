"""The search aggregator must never synthesise sources.

Production failure: every search provider failed (Playwright browser closed,
SearXNG unreachable), and ``aggregate_search`` fell back to inventing two
results pointing at ``docs.searchops.dev/research/<hash>`` and
``github.com/searchops/architecture-wiki/<hash>``. Those hosts do not resolve.
The pipeline then reported 3 "sources" discovered, scraped all 3, failed all
3, and produced a report whose sources were never actually read.

An honest empty result lets the critic emit a gap and the job report that
nothing was found.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from searchops.search.aggregator import FederatedSearchAggregator
from searchops.search.contracts import SearchQuery

pytestmark = pytest.mark.unit


def _no_fabricated_urls(results) -> None:
    for r in results:
        assert "searchops" not in r.url.lower(), f"fabricated URL returned: {r.url}"


@pytest.mark.asyncio
async def test_returns_empty_when_every_provider_fails():
    agg = FederatedSearchAggregator()

    with patch(
        "searchops.search.orchestrator.search_orchestrator.search",
        new=AsyncMock(return_value=[]),
    ):
        results = await agg.aggregate_search(SearchQuery(query="what is API security"))

    assert results == []
    _no_fabricated_urls(results)


@pytest.mark.asyncio
async def test_never_invents_results_when_search_raises():
    """A raising provider must not be replaced with synthetic sources."""
    agg = FederatedSearchAggregator()

    with patch(
        "searchops.search.orchestrator.search_orchestrator.search",
        new=AsyncMock(side_effect=RuntimeError("all providers down")),
    ):
        with pytest.raises(RuntimeError):
            await agg.aggregate_search(SearchQuery(query="what is API security"))


@pytest.mark.asyncio
async def test_real_results_are_returned_unchanged():
    from searchops.search.contracts import SearchResultItem

    real = SearchResultItem(
        title="OWASP API Security Top 10",
        url="https://owasp.org/API-Security/editions/2023/en/0x11-t10/",
        snippet="Real source",
        score=0.9,
        provider="tavily",
    )
    agg = FederatedSearchAggregator()

    with patch(
        "searchops.search.orchestrator.search_orchestrator.search",
        new=AsyncMock(return_value=[real]),
    ):
        results = await agg.aggregate_search(SearchQuery(query="api security"))

    assert results == [real]
    assert results[0].url.startswith("https://owasp.org")
