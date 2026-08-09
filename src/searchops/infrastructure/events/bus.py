"""
Redis-backed Event Bus for Domain Event Pub/Sub.
"""

from __future__ import annotations

import orjson
import structlog

from searchops.infrastructure.cache.redis import RedisCache, get_redis_client

log = structlog.get_logger(__name__)


class RedisEventBus:
    """Publishes and subscribes to domain events via Redis."""

    def __init__(self, cache: RedisCache | None = None) -> None:
        self.cache = cache or RedisCache(get_redis_client())

    async def publish(self, channel: str, event_data: dict) -> None:
        """Publish event payload to Redis channel."""
        try:
            payload = orjson.dumps(event_data)
            await self.cache.client.publish(channel, payload)
            log.info("Published domain event", channel=channel)
        except Exception as exc:
            log.error("Failed to publish event", channel=channel, error=str(exc))
