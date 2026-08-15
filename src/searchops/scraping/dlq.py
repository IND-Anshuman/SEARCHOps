"""
Scrape Dead-Letter Queue (DLQ) & Failure Recovery Manager.
"""

from __future__ import annotations

import time
import structlog
from pydantic import BaseModel, Field

from searchops.infrastructure.cache.redis import RedisCache, get_redis_client

log = structlog.get_logger(__name__)


class FailedScrapeRecord(BaseModel):
    url: str
    error_message: str
    retry_count: int = 0
    failed_at: float = Field(default_factory=time.time)


class ScrapeDLQManager:
    """Manages Dead-Letter Queue for failed scrape URLs with retry policies."""

    def __init__(self, cache: RedisCache | None = None) -> None:
        self.cache = cache or RedisCache(get_redis_client())

    async def record_failure(self, url: str, error: Exception, retry_count: int = 1) -> None:
        """Record scrape failure to Redis Dead-Letter Queue."""
        record = FailedScrapeRecord(
            url=url,
            error_message=str(error),
            retry_count=retry_count,
        )
        dlq_key = f"dlq:scrape:{url}"
        await self.cache.set(dlq_key, record.model_dump(), ttl_seconds=86400 * 7)
        log.warning("Recorded scrape failure to DLQ", url=url, retries=retry_count, error=str(error))

    async def is_in_dlq(self, url: str) -> bool:
        """Return True if URL is currently quarantined in Dead-Letter Queue."""
        dlq_key = f"dlq:scrape:{url}"
        return await self.cache.exists(dlq_key)
