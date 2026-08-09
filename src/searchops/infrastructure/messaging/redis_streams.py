"""
Redis Streams Event Bus Implementation.

Implements `IEventBus` from `core.interfaces.event_bus`.
Publishes and subscribes to events using Redis Streams (XADD, XREADGROUP).
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import orjson
import redis.asyncio as aioredis
import structlog

from searchops.core.exceptions.infrastructure import MessagingError
from searchops.core.interfaces.event_bus import IEventBus, IEventHandler
from searchops.infrastructure.cache.redis import get_redis_client
from searchops.shared.domain.event import DomainEvent

log = structlog.get_logger(__name__)


class RedisStreamsEventBus(IEventBus):
    """Event Bus backed by Redis Streams for asynchronous decoupling."""

    def __init__(
        self,
        client: aioredis.Redis | None = None,
        stream_prefix: str = "searchops:events:",
    ) -> None:
        self.client = client or get_redis_client()
        self.stream_prefix = stream_prefix
        self._handlers: dict[str, list[IEventHandler]] = {}

    def _get_stream_key(self, topic: str) -> str:
        return f"{self.stream_prefix}{topic}"

    async def publish(self, topic: str, event: DomainEvent) -> str:
        """Publish a DomainEvent to a Redis Stream topic.

        Returns:
            The Redis Stream message ID.
        """
        try:
            stream_key = self._get_stream_key(topic)
            payload = event.model_dump_json()
            message_data = {
                "event_type": event.event_type,
                "event_id": event.event_id,
                "payload": payload,
            }
            msg_id = await self.client.xadd(stream_key, message_data)
            log.debug(
                "Event published",
                topic=topic,
                event_type=event.event_type,
                event_id=event.event_id,
                msg_id=msg_id,
            )
            return str(msg_id)
        except Exception as exc:
            log.error("Failed to publish event", topic=topic, event_id=event.event_id, error=str(exc))
            raise MessagingError(f"Publish failed for topic {topic}", cause=exc) from exc

    async def publish_batch(self, topic: str, events: list[DomainEvent]) -> list[str]:
        """Publish multiple events atomically using a pipeline."""
        try:
            stream_key = self._get_stream_key(topic)
            async with self.client.pipeline(transaction=True) as pipe:
                for event in events:
                    payload = event.model_dump_json()
                    pipe.xadd(
                        stream_key,
                        {
                            "event_type": event.event_type,
                            "event_id": event.event_id,
                            "payload": payload,
                        },
                    )
                msg_ids = await pipe.execute()
            return [str(mid) for mid in msg_ids]
        except Exception as exc:
            log.error("Failed to publish batch", topic=topic, count=len(events), error=str(exc))
            raise MessagingError(f"Publish batch failed for topic {topic}", cause=exc) from exc

    async def subscribe(self, topic: str, handler: IEventHandler) -> None:
        """Register an in-process handler for a topic."""
        if topic not in self._handlers:
            self._handlers[topic] = []
        if handler not in self._handlers[topic]:
            self._handlers[topic].append(handler)
            log.info("Registered event handler", topic=topic, handler=handler.__class__.__name__)

    async def unsubscribe(self, topic: str, handler: IEventHandler) -> None:
        """Unregister an in-process handler."""
        if topic in self._handlers and handler in self._handlers[topic]:
            self._handlers[topic].remove(handler)
