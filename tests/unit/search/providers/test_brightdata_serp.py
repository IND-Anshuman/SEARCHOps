"""
Unit tests for BrightDataSerpProvider.

All tests use mocked httpx to avoid real API calls.
Run: uv run pytest tests/unit/search/providers/test_brightdata_serp.py -v
"""
from __future__ import annotations

import pytest
import respx
import httpx
from unittest.mock import patch

from searchops.search.providers.brightdata_serp import BrightDataSerpProvider
from searchops.search.contracts import SearchQuery
from searchops.search.domain.models import SearchProfile


BD_SERP_API_URL = "https://api.brightdata.com/serp/req"

MOCK_SERP_RESPONSE = {
    "organic": [
        {
            "title": "LangGraph Documentation",
            "link": "https://python.langchain.com/docs/langgraph",
            "snippet": "LangGraph is a library for building stateful, multi-actor applications with LLMs.",
            "position": 1,
            "displayed_link": "python.langchain.com",
            "date": "2024-11-01",
        },
        {
            "title": "LangGraph GitHub Repository",
            "link": "https://github.com/langchain-ai/langgraph",
            "snippet": "Build stateful, multi-actor applications with LLMs.",
            "position": 2,
            "displayed_link": "github.com",
        },
    ],
    "people_also_ask": [
        {"question": "What is LangGraph used for?"},
        {"question": "How does LangGraph differ from LangChain?"},
        {"question": "Is LangGraph free to use?"},
    ],
    "knowledge_graph": {
        "title": "LangGraph",
        "type": "Software library",
        "description": "Open-source framework for building LLM-powered agents.",
    },
    "related_searches": [
        {"query": "langgraph tutorial"},
        {"query": "langgraph vs autogen"},
    ],
}


@pytest.fixture
def provider_with_key(monkeypatch):
    """Provider with a fake API key injected."""
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "fake-bd-api-key-for-testing")
    monkeypatch.setenv("BRIGHTDATA_ZONE_SERP", "serp")
    # Clear settings cache to pick up monkeypatched env vars
    from searchops.config.settings import get_settings
    get_settings.cache_clear()
    provider = BrightDataSerpProvider()
    yield provider
    get_settings.cache_clear()


@pytest.fixture
def provider_no_key(monkeypatch):
    """Provider without an API key."""
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    from searchops.config.settings import get_settings
    get_settings.cache_clear()
    provider = BrightDataSerpProvider()
    yield provider
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_organic_results(provider_with_key):
    """Should return normalized SearchResultItems from organic SERP results."""
    respx.post(BD_SERP_API_URL).mock(
        return_value=httpx.Response(200, json=MOCK_SERP_RESPONSE)
    )
    query = SearchQuery(query="langgraph framework", max_results=10)
    results = await provider_with_key.search(query)

    assert len(results) == 2
    assert results[0].url == "https://python.langchain.com/docs/langgraph"
    assert results[0].title == "LangGraph Documentation"
    assert results[0].provider == "brightdata_serp"
    assert results[0].rank == 1


@pytest.mark.asyncio
@respx.mock
async def test_paa_subqueries_in_metadata(provider_with_key):
    """PAA questions should be stored in raw_metadata of each result."""
    respx.post(BD_SERP_API_URL).mock(
        return_value=httpx.Response(200, json=MOCK_SERP_RESPONSE)
    )
    query = SearchQuery(query="langgraph", max_results=10)
    results = await provider_with_key.search(query)

    assert len(results) > 0
    paa = results[0].raw_metadata.get("paa_subqueries", [])
    assert len(paa) == 3
    assert "What is LangGraph used for?" in paa


@pytest.mark.asyncio
@respx.mock
async def test_knowledge_graph_in_metadata(provider_with_key):
    """Knowledge Graph card should be stored in raw_metadata."""
    respx.post(BD_SERP_API_URL).mock(
        return_value=httpx.Response(200, json=MOCK_SERP_RESPONSE)
    )
    query = SearchQuery(query="langgraph", max_results=10)
    results = await provider_with_key.search(query)

    kg = results[0].raw_metadata.get("knowledge_graph", {})
    assert kg.get("title") == "LangGraph"
    assert kg.get("type") == "Software library"


@pytest.mark.asyncio
async def test_search_missing_key_returns_empty(provider_no_key):
    """Missing API key should return empty list without raising an exception."""
    query = SearchQuery(query="langgraph", max_results=5)
    results = await provider_no_key.search(query)
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_search_api_error_returns_empty(provider_with_key):
    """Non-200 API response should return empty list without raising."""
    respx.post(BD_SERP_API_URL).mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    query = SearchQuery(query="langgraph", max_results=5)
    results = await provider_with_key.search(query)
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_empty_query_returns_empty(provider_with_key):
    """Empty query string should return empty list without calling the API."""
    query = SearchQuery(query="   ", max_results=5)
    results = await provider_with_key.search(query)
    # Should not have hit the API
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_geo_targeting_country_in_url(provider_with_key):
    """Country code should be included in the Google URL sent to BD SERP API."""
    import json as json_lib
    captured_request = None

    def capture(req):
        nonlocal captured_request
        captured_request = req
        return httpx.Response(200, json=MOCK_SERP_RESPONSE)

    respx.post(BD_SERP_API_URL).mock(side_effect=capture)

    query = SearchQuery(
        query="AI trends",
        max_results=5,
        raw_metadata={"country": "de"},
    )
    await provider_with_key.search(query)

    assert captured_request is not None
    body = json_lib.loads(captured_request.content.decode())
    # The 'url' field in the payload should contain gl=de for German geo-targeting
    assert "gl=de" in body["url"]


def test_provider_capabilities():
    """Provider must declare the expected premium capabilities."""
    from searchops.search.domain.models import SearchCapability
    provider = BrightDataSerpProvider.__new__(BrightDataSerpProvider)
    provider._search_cfg = type("c", (), {"brightdata_api_key": None, "brightdata_zone_serp": "serp", "search_timeout": 30})()
    provider._scraping_cfg = None
    provider._api_key = None
    provider._zone = "serp"

    assert SearchCapability.SERP_FEATURES in provider.capabilities
    assert SearchCapability.ANTI_BOT in provider.capabilities
    assert SearchCapability.LOCALIZATION in provider.capabilities


def test_provider_name():
    """Provider name must be exactly 'brightdata_serp'."""
    provider = BrightDataSerpProvider.__new__(BrightDataSerpProvider)
    provider._api_key = None
    provider._zone = "serp"
    assert provider.name == "brightdata_serp"


def test_cost_per_query():
    """Cost should be $0.001 per query."""
    provider = BrightDataSerpProvider.__new__(BrightDataSerpProvider)
    provider._api_key = None
    provider._zone = "serp"
    assert provider.cost_per_query == 0.001
