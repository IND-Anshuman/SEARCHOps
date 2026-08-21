"""
Unit tests for scraping cache, DLQ manager, transport pool, and scraper implementations.
"""

from __future__ import annotations

import pytest
from searchops.core.interfaces.scraper import ScrapeRequest, ScrapeResult, ScrapeMode
from searchops.scraping.cache import ContentHashCache
from searchops.scraping.dlq import ScrapeDLQManager, FailedScrapeRecord
from searchops.scraping.transport import get_transport_pool, close_transport_pool
from searchops.scraping.firecrawl import FirecrawlScraper
from searchops.scraping.playwright import PlaywrightScraper


class MockCache:
    def __init__(self):
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: dict, ttl_seconds: int = 3600):
        self.store[key] = value

    async def exists(self, key: str) -> bool:
        return key in self.store


@pytest.mark.unit
@pytest.mark.asyncio
async def test_content_hash_cache():
    mock_redis = MockCache()
    cache = ContentHashCache(cache=mock_redis)

    url = "https://example.com/test"
    payload = {"url": url, "content": "Hello World"}

    await cache.set_cached_scrape(url, payload)
    retrieved = await cache.get_cached_scrape(url)

    assert retrieved is not None
    assert retrieved["content"] == "Hello World"
    assert retrieved["content_hash"] == cache.compute_sha256("Hello World")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scrape_dlq_manager():
    mock_redis = MockCache()
    dlq = ScrapeDLQManager(cache=mock_redis)

    url = "https://example.com/failed"
    assert await dlq.is_in_dlq(url) is False

    await dlq.record_failure(url, ValueError("Timeout error"), retry_count=2)
    assert await dlq.is_in_dlq(url) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_pool_lifecycle():
    client = get_transport_pool()
    assert client is not None
    assert not client.is_closed

    await close_transport_pool()
    client_after = get_transport_pool()
    assert client_after is not None
    await close_transport_pool()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_firecrawl_scraper_missing_key():
    scraper = FirecrawlScraper()
    scraper.api_key = None
    req = ScrapeRequest(url="https://example.com")
    res = await scraper.scrape(req)
    assert res.status_code == 401
    assert res.scrape_mode_used == ScrapeMode.FIRECRAWL
    assert await scraper.health_check() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_playwright_scraper_health():
    scraper = PlaywrightScraper()
    health = await scraper.health_check()
    assert isinstance(health, bool)
