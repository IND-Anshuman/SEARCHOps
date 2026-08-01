"""
A2A Protocol Dead-Letter Queue (DLQ) Stream Handler.

Persists unroutable or failed inter-agent messages to Redis Stream / Memory
for inspection and administrative redelivery.
"""

from __future__ import annotations

from typing import Any

import structlog

from searchops.a2a.protocol.envelopes import A2AMessageEnvelope

log = structlog.get_logger(__name__)


class A2ADeadLetterQueue:
    """Dead-Letter Queue handler for unroutable agent message envelopes."""

    def __init__(self, redis_client: Any | None = None, stream_key: str = "a2a:dlq") -> None:
        self.redis_client = redis_client
        self.stream_key = stream_key
        self._memory_dlq: list[dict[str, Any]] = []

    async def push_failed_envelope(self, envelope: A2AMessageEnvelope, reason: str) -> None:
        """Push failed message envelope to DLQ with failure reason."""
        entry = {
            "message_id": envelope.message_id,
            "sender_id": envelope.sender_id,
            "recipient_id": envelope.recipient_id,
            "message_type": str(envelope.message_type),
            "reason": reason,
            "payload": str(envelope.payload),
        }

        if self.redis_client:
            try:
                await self.redis_client.xadd(self.stream_key, entry)
                log.warning("Pushed failed A2A envelope to Redis DLQ stream", message_id=envelope.message_id, reason=reason)
                return
            except Exception as exc:
                log.error("Failed to push to Redis DLQ stream, using memory fallback", error=str(exc))

        self._memory_dlq.append(entry)
        log.warning("Pushed failed A2A envelope to memory DLQ", message_id=envelope.message_id, reason=reason)

    async def get_failed_count(self) -> int:
        """Return total failed envelopes currently in DLQ."""
        if self.redis_client:
            try:
                return await self.redis_client.xlen(self.stream_key)
            except Exception:
                pass
        return len(self._memory_dlq)
