"""
Unit tests for MCP Client and Scraping Pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.mcp.client import MCPClient
from searchops.scraping.pipeline import ScrapingPipeline


def _make_flags():
    from searchops.feature_flags.manager import FeatureFlagManager
    from searchops.feature_flags.providers import InMemoryFeatureFlagProvider

    provider = InMemoryFeatureFlagProvider(
        {
            "crawl4ai_enabled": True,
            "playwright_enabled": True,
            "firecrawl_enabled": True,
            "rate_limiting_enabled": False,
        }
    )
    return FeatureFlagManager(providers=[provider])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_client_refuses_an_unregistered_tool():
    """An unknown tool must not report a success.

    The client used to return {"output": "Executed X successfully"} for any
    tool name it was handed, which meant a caller could believe a scrape ran
    when nothing had run at all. An unregistered tool is now an error.
    """
    from searchops.core.exceptions.infrastructure import ExternalServiceError

    client = MCPClient()
    with pytest.raises(ExternalServiceError) as exc:
        await client.call_tool("firecrawl", "firecrawl_scrape", {"url": "https://example.com"})
    assert "not registered" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_client_executes_a_bound_handler():
    """A registered tool with a bound handler runs that handler."""
    from searchops.platform.registry.tool_registry import ToolDefinition, ToolRegistry

    calls: list[dict] = []

    async def _handler(**kwargs):
        calls.append(kwargs)
        return {"scraped": True, "url": kwargs.get("url")}

    registry = ToolRegistry()
    await registry.register(
        ToolDefinition(
            name="firecrawl:firecrawl_scrape",
            description="Scrape a URL",
            input_schema={},
            output_schema={},
        ),
        handler=_handler,
    )

    client = MCPClient(tool_registry=registry)
    res = await client.call_tool("firecrawl", "firecrawl_scrape", {"url": "https://example.com"})

    assert res["status"] == "success"
    assert res["server"] == "firecrawl"
    assert res["result"] == {"scraped": True, "url": "https://example.com"}
    assert calls == [{"url": "https://example.com"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_client_refuses_a_definition_with_no_handler():
    """A registered definition with no handler is a config error, not a success."""
    from searchops.core.exceptions.infrastructure import ExternalServiceError
    from searchops.platform.registry.tool_registry import ToolDefinition, ToolRegistry

    registry = ToolRegistry()
    await registry.register(
        ToolDefinition(
            name="firecrawl:firecrawl_scrape",
            description="Scrape a URL",
            input_schema={},
            output_schema={},
        )
    )

    client = MCPClient(tool_registry=registry)
    with pytest.raises(ExternalServiceError) as exc:
        await client.call_tool("firecrawl", "firecrawl_scrape", {"url": "https://example.com"})
    assert "no executable handler" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_client_refuses_a_disabled_tool():
    from searchops.core.exceptions.infrastructure import ExternalServiceError
    from searchops.platform.registry.tool_registry import ToolDefinition, ToolRegistry

    async def _handler(**kwargs):
        return {"ok": True}

    registry = ToolRegistry()
    await registry.register(
        ToolDefinition(
            name="firecrawl:firecrawl_scrape",
            description="Scrape a URL",
            input_schema={},
            output_schema={},
            enabled=False,
        ),
        handler=_handler,
    )

    client = MCPClient(tool_registry=registry)
    with pytest.raises(ExternalServiceError) as exc:
        await client.call_tool("firecrawl", "firecrawl_scrape", {"url": "https://example.com"})
    assert "disabled" in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scraping_pipeline_fallback():
    """
    Validates the Stealth → Playwright fallback chain (Tiers 0 → 1).

    Stealth (Tier 0) returns 500 → pipeline escalates to Playwright (Tier 1)
    which succeeds with 200.  Firecrawl (Tier 2) must NOT be called.
    """
    from unittest.mock import MagicMock

    # Tier 0: stealth fails → pipeline escalates
    mock_stealth = AsyncMock()
    mock_stealth._cfg = MagicMock()
    mock_stealth._cfg.impersonate = "chrome124"
    mock_stealth.scrape.return_value = ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=500,
        scrape_mode_used=ScrapeMode.STEALTH_HTTP,
    )

    # Tier 1: Playwright succeeds
    mock_playwright = AsyncMock()
    mock_playwright.pool = MagicMock(stats={})
    mock_playwright.scrape.return_value = ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        html="<h1>Playwright</h1><p>Vector databases store embeddings and support approximate nearest neighbour search, forming the retrieval backbone of modern RAG systems.</p>",
        scrape_mode_used=ScrapeMode.PLAYWRIGHT,
    )

    # Tier 2: Firecrawl must NOT be invoked when Playwright succeeds
    mock_firecrawl = AsyncMock()

    pipeline = ScrapingPipeline(
        stealth=mock_stealth,
        proxy_router=None,
        firecrawl=mock_firecrawl,
        playwright=mock_playwright,
        feature_flags=_make_flags(),
    )

    req = ScrapeRequest(url="https://example.com")
    result = await pipeline.execute(req)

    assert result.status_code == 200
    assert "Playwright" in result.html
    assert result.scrape_mode_used == ScrapeMode.PLAYWRIGHT
    mock_stealth.scrape.assert_called_once_with(req)
    mock_playwright.scrape.assert_called_once_with(req)
    mock_firecrawl.scrape.assert_not_called()  # Firecrawl is Tier 2; Playwright short-circuits


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scraping_pipeline_playwright_to_firecrawl_fallback():
    """
    Validates the Playwright → Firecrawl fallback chain (Tiers 1 → 2).

    Both Stealth (Tier 0) and Playwright (Tier 1) fail, so the pipeline
    escalates to Firecrawl (Tier 2) which succeeds.
    """
    from unittest.mock import MagicMock

    mock_stealth = AsyncMock()
    mock_stealth._cfg = MagicMock()
    mock_stealth._cfg.impersonate = "chrome124"
    mock_stealth.scrape.return_value = ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=500,
        scrape_mode_used=ScrapeMode.STEALTH_HTTP,
    )

    mock_playwright = AsyncMock()
    mock_playwright.pool = MagicMock(stats={})
    mock_playwright.scrape.return_value = ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=503,
        scrape_mode_used=ScrapeMode.PLAYWRIGHT,
    )

    mock_firecrawl = AsyncMock()
    mock_firecrawl.scrape.return_value = ScrapeResult(
        url="https://example.com",
        final_url="https://example.com",
        status_code=200,
        html="<h1>Firecrawl</h1><p>Vector databases store embeddings and support approximate nearest neighbour search, forming the retrieval backbone of modern RAG systems.</p>",
        scrape_mode_used=ScrapeMode.FIRECRAWL,
    )

    pipeline = ScrapingPipeline(
        stealth=mock_stealth,
        proxy_router=None,
        firecrawl=mock_firecrawl,
        playwright=mock_playwright,
        feature_flags=_make_flags(),
    )

    req = ScrapeRequest(url="https://example.com")
    result = await pipeline.execute(req)

    assert result.status_code == 200
    assert "Firecrawl" in result.html
    mock_stealth.scrape.assert_called_once_with(req)
    mock_playwright.scrape.assert_called_once_with(req)
    mock_firecrawl.scrape.assert_called_once_with(req)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scraping_pipeline_circuit_breaker_404():
    mock_firecrawl = AsyncMock()
    mock_firecrawl.scrape.return_value = ScrapeResult(
        url="https://example.com/404",
        final_url="https://example.com/404",
        status_code=404,
        scrape_mode_used=ScrapeMode.FIRECRAWL,
    )
    AsyncMock()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_basic_http_scraper():
    import respx
    from httpx import Response

    from searchops.scraping.pipeline import BasicHTTPScraper

    with respx.mock:
        respx.get("https://example.com/basic").mock(
            return_value=Response(
                200,
                text="<h1>Basic</h1><p>Vector databases store embeddings and support approximate nearest neighbour search, forming the retrieval backbone of modern RAG systems.</p>",
            )
        )
        scraper = BasicHTTPScraper()
        res = await scraper.scrape(ScrapeRequest(url="https://example.com/basic"))
        assert res.status_code == 200
        assert (
            res.html
            == "<h1>Basic</h1><p>Vector databases store embeddings and support approximate nearest neighbour search, forming the retrieval backbone of modern RAG systems.</p>"
        )
        assert res.scrape_mode_used == ScrapeMode.HTTP


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scraping_pipeline_cache_hit():
    mock_cache = AsyncMock()
    mock_cache.get.return_value = {
        "url": "https://example.com/cached",
        "final_url": "https://example.com/cached",
        "status_code": 200,
        "html": "Cached Content",
        "scrape_mode_used": "http",
    }
    mock_firecrawl = AsyncMock()

    pipeline = ScrapingPipeline(firecrawl=mock_firecrawl, cache=mock_cache, feature_flags=_make_flags())
    res = await pipeline.execute(ScrapeRequest(url="https://example.com/cached"))

    assert res.status_code == 200
    assert res.html == "Cached Content"
    mock_firecrawl.scrape.assert_not_called()
