"""
Unit tests for A2A Protocol, Router, and Handshake Manager.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from searchops.a2a.handshake import A2AHandshakeManager
from searchops.a2a.protocol.envelopes import (
    A2AMessageEnvelope,
    A2AMessageType,
    AgentHandshakeRequest,
    JsonRpcRequest,
)
from searchops.a2a.router.router import A2AMessageRouter
from searchops.a2a.transports.http import IA2ATransport
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.typing.aliases import AgentId


class DummyLocalAgent:
    async def handle_a2a_message(self, envelope: A2AMessageEnvelope) -> A2AMessageEnvelope:
        return A2AMessageEnvelope(
            message_type=A2AMessageType.TASK_RESPONSE,
            sender_id=envelope.recipient_id,
            recipient_id=envelope.sender_id,
            payload={"status": "success", "echo": envelope.payload},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a2a_handshake_manager():
    agent_reg = AgentRegistry()
    cap_reg = CapabilityRegistry()
    manager = A2AHandshakeManager(agent_reg, cap_reg)

    req = AgentHandshakeRequest(
        agent_id=AgentId("remote-agent-1"),
        name="Remote Search Agent",
        version="1.0.0",
        capabilities=["web_search", "scraping"],
        endpoint="http://remote-agent:8080/a2a",
    )

    res = await manager.handle_handshake(req)
    assert res.success is True

    card = await agent_reg.get(AgentId("remote-agent-1"))
    assert card is not None
    assert card.name == "Remote Search Agent"

    search_agents = await cap_reg.get_agents_for_capability("web_search")
    assert search_agents == ["remote-agent-1"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a2a_local_message_routing():
    agent_reg = AgentRegistry()
    cap_reg = CapabilityRegistry()
    mock_transport = AsyncMock(spec=IA2ATransport)
    router = A2AMessageRouter(agent_reg, cap_reg, transport=mock_transport)

    local_agent = DummyLocalAgent()
    router.register_local_agent(AgentId("local-agent-1"), local_agent)

    envelope = A2AMessageEnvelope(
        message_type=A2AMessageType.TASK_REQUEST,
        sender_id=AgentId("caller-1"),
        recipient_id=AgentId("local-agent-1"),
        payload={"query": "test local routing"},
    )

    response = await router.route(envelope)
    assert response.sender_id == "local-agent-1"
    assert response.recipient_id == "caller-1"
    assert response.payload["status"] == "success"
    mock_transport.send.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a2a_remote_capability_routing():
    agent_reg = AgentRegistry()
    cap_reg = CapabilityRegistry()
    mock_transport = AsyncMock(spec=IA2ATransport)

    router = A2AMessageRouter(agent_reg, cap_reg, transport=mock_transport)
    handshake_mgr = A2AHandshakeManager(agent_reg, cap_reg)

    await handshake_mgr.handle_handshake(
        AgentHandshakeRequest(
            agent_id=AgentId("remote-researcher"),
            name="Remote Researcher",
            version="1.0",
            capabilities=["deep_research"],
            endpoint="http://remote-host:9000/a2a",
        )
    )

    envelope = A2AMessageEnvelope(
        message_type=A2AMessageType.TASK_REQUEST,
        sender_id=AgentId("planner"),
        recipient_id=AgentId("temp"),
        payload={"task": "deep search"},
    )

    expected_resp = A2AMessageEnvelope(
        message_type=A2AMessageType.TASK_RESPONSE,
        sender_id=AgentId("remote-researcher"),
        recipient_id=AgentId("planner"),
        payload={"result": "done"},
    )
    mock_transport.send.return_value = expected_resp

    res = await router.route_by_capability("deep_research", envelope)

    assert res.sender_id == "remote-researcher"
    mock_transport.send.assert_called_once_with("http://remote-host:9000/a2a", envelope)
