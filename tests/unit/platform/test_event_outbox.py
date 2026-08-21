"""
Unit tests for TransactionalEventOutbox.
"""

from __future__ import annotations

import pytest
from searchops.platform.events.outbox import TransactionalEventOutbox


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_outbox_memory_publishing():
    outbox = TransactionalEventOutbox(redis_client=None)

    await outbox.publish_event(
        event_type="ResearchStarted",
        payload={"job_id": "job_123", "query": "Quantum Computing"},
        correlation_id="corr_456",
    )

    processed_count = await outbox.process_outbox_queue()
    assert processed_count == 1
