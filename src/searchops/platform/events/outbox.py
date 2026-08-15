"""
Transactional Event Outbox Bus Infrastructure.

Implements the Outbox pattern for publishing domain events
(e.g., ResearchStarted, EntityDiscovered, ReportGenerated) to Redis Streams.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class TransactionalEventOutbox:
    """Transactional Outbox repository and event stream publisher."""

    def __init__(self, redis_client: Any | None = None, stream_key: str = "searchops:events") -> None:
        self.redis_client = redis_client
        self.stream_key = stream_key
        self._outbox_store: list[dict[str, Any]] = []

    async def publish_event(self, event_type: str, payload: dict[str, Any], correlation_id: str = "") -> None:
        """Publish domain event to Transactional Outbox stream."""
        event_entry = {
            "event_type": event_type,
            "correlation_id": correlation_id,
            "timestamp": str(time.time()),
            "payload": str(payload),
            "status": "pending",
        }

        if self.redis_client:
            try:
                await self.redis_client.xadd(self.stream_key, event_entry)
                log.info("Published domain event to Redis Outbox stream", event_type=event_type, correlation_id=correlation_id)
                return
            except Exception as exc:
                log.error("Failed to publish event to Redis stream, saving to memory outbox", error=str(exc))

        self._outbox_store.append(event_entry)
        log.info("Saved domain event to memory Outbox store", event_type=event_type, correlation_id=correlation_id)

    async def process_outbox_queue(self) -> int:
        """Process and flush pending outbox messages."""
        if not self._outbox_store:
            return 0

        flushed = 0
        pending = list(self._outbox_store)
        self._outbox_store.clear()

        for item in pending:
            item["status"] = "delivered"
            if self.redis_client:
                try:
                    await self.redis_client.xadd(self.stream_key, item)
                    flushed += 1
                except Exception as exc:
                    log.error("Failed to relay outbox item to Redis stream", error=str(exc))
                    self._outbox_store.append(item)
            else:
                flushed += 1

        log.info("Processed outbox queue", flushed=flushed)
        return flushed

    async def process_pending_events(self, bus: Any | None = None) -> int:
        """Relay pending outbox events to event bus or Redis stream."""
        return await self.process_outbox_queue()

