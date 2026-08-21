"""
Unit tests for Serper and Tavily Search Providers.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from searchops.search.contracts import SearchQuery
from searchops.search.providers.serper import SerperSearchProvider
from searchops.search.providers.tavily import TavilySearchProvider


@pytest.mark.unit
@pytest.mark.asyncio
async def test_serper_search_provider_missing_key():
    provider = SerperSearchProvider()
    provider.api_key = None
    assert provider.name == "serper"
    results = await provider.search(SearchQuery(query="test query"))
    assert results == []


@pytest.mark.unit
@pytest.mark.asyncio
@respx.mock
async def test_serper_search_provider_success():
    respx.post("https://google.serper.dev/search").mock(
        return_value=Response(
            200,
            json={
                "organic": [
                    {"title": "Test Title", "link": "https://example.com", "snippet": "Test snippet"}
                ]
            },
        )
    )

    provider = SerperSearchProvider()
    provider.api_key = "test-serper-key"
    results = await provider.search(SearchQuery(query="python"))
    assert len(results) == 1
    assert results[0].title == "Test Title"
    assert results[0].url == "https://example.com"
    assert results[0].provider == "serper"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tavily_search_provider_missing_key():
    provider = TavilySearchProvider()
    provider.api_key = None
    assert provider.name == "tavily"
    results = await provider.search(SearchQuery(query="test query"))
    assert results == []


@pytest.mark.unit
@pytest.mark.asyncio
@respx.mock
async def test_tavily_search_provider_success():
    respx.post("https://api.tavily.com/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "title": "Tavily Result",
                        "url": "https://tavily.com/res",
                        "content": "Tavily snippet",
                        "score": 0.9,
                    }
                ]
            },
        )
    )

    provider = TavilySearchProvider()
    provider.api_key = "test-tavily-key"
    results = await provider.search(SearchQuery(query="ai agents"))
    assert len(results) == 1
    assert results[0].title == "Tavily Result"
    assert results[0].url == "https://tavily.com/res"
    assert results[0].score == 0.9
    assert results[0].provider == "tavily"
