"""
ETag & SHA256 Content-Hash Cache for Scraping Ingestion.
"""

from __future__ import annotations

import hashlib
import orjson
import structlog

from searchops.infrastructure.cache.redis import RedisCache, get_redis_client

log = structlog.get_logger(__name__)


class ContentHashCache:
    """Caching layer verifying URL content hashes (SHA256) & ETags before re-scraping."""

    def __init__(self, cache: RedisCache | None = None) -> None:
        self.cache = cache or RedisCache(get_redis_client())

    @staticmethod
    def compute_sha256(content: str) -> str:
        """Compute SHA256 hex digest of document text."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def get_cached_scrape(self, url: str) -> dict | None:
        """Retrieve cached scrape payload by URL hash key."""
        key = f"scrape_cache:{hashlib.sha256(url.encode()).hexdigest()}"
        return await self.cache.get(key)

    async def set_cached_scrape(self, url: str, payload: dict, ttl_seconds: int = 86400) -> None:
        """Cache scrape result with SHA256 content metadata."""
        if "content" in payload and payload["content"]:
            payload["content_hash"] = self.compute_sha256(payload["content"])

        key = f"scrape_cache:{hashlib.sha256(url.encode()).hexdigest()}"
        await self.cache.set(key, payload, ttl_seconds=ttl_seconds)
        log.info("Cached scrape payload", url=url, hash=payload.get("content_hash"))
