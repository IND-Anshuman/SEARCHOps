"""
Unit tests for A2ADeadLetterQueue and A2AMessageRouter DLQ integration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from searchops.a2a.dlq import A2ADeadLetterQueue
from searchops.a2a.protocol.envelopes import A2AMessageEnvelope, A2AMessageType
from searchops.a2a.router.router import A2AMessageRouter
from searchops.core.exceptions.domain import EntityNotFoundError


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dlq_memory_push():
    dlq = A2ADeadLetterQueue(redis_client=None)
    env = A2AMessageEnvelope(
        message_type=A2AMessageType.TASK_REQUEST,
        sender_id="agent_1",
        recipient_id="agent_2",
        payload={"msg": "hi"},
    )
    await dlq.push_failed_envelope(env, reason="Unroutable")

    count = await dlq.get_failed_count()
    assert count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_router_dlq_fallback_on_error():
    mock_agent_reg = AsyncMock()
    mock_agent_reg.get.return_value = None  # Not found
    mock_cap_reg = AsyncMock()
    mock_dlq = AsyncMock()

    router = A2AMessageRouter(
        agent_registry=mock_agent_reg,
        capability_registry=mock_cap_reg,
        dlq=mock_dlq,
    )

    env = A2AMessageEnvelope(
        message_type=A2AMessageType.TASK_REQUEST,
        sender_id="agent_1",
        recipient_id="unknown_agent",
        payload={},
    )

    with pytest.raises(EntityNotFoundError):
        await router.route(env)

    mock_dlq.push_failed_envelope.assert_called_once_with(env, reason="AgentCard with id 'unknown_agent' not found")
