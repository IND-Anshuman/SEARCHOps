"""
Unit tests for scraping cache, transport, and DLQ components.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from searchops.scraping.cache import ContentHashCache
from searchops.scraping.dlq import ScrapeDLQManager
from searchops.scraping.transport import get_transport_pool, close_transport_pool


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scraping_cache_full():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = {"title": "Test Page", "content": "Sample content"}

    cache = ContentHashCache(cache=mock_redis)
    res = await cache.get_cached_scrape("https://example.com")
    assert res is not None
    assert res["title"] == "Test Page"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scraping_dlq_full():
    mock_redis = AsyncMock()
    dlq = ScrapeDLQManager(cache=mock_redis)

    await dlq.record_failure("https://failed.com", ValueError("500 Server Error"))
    mock_redis.set.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_pool_full():
    pool = get_transport_pool()
    assert pool is not None
    await close_transport_pool()

