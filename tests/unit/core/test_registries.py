from __future__ import annotations

import pytest

from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.tool_registry import ToolRegistry, ToolDefinition
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.core.interfaces.agent import AgentCard, AgentCapability
from searchops.typing.aliases import AgentId


def make_agent_card(agent_id: str = "agent-1", capability_name: str = "search") -> AgentCard:
    return AgentCard(
        agent_id=AgentId(agent_id),
        name=f"Test Agent {agent_id}",
        description="Test description",
        version="1.0.0",
        capabilities=[AgentCapability(name=capability_name, description="Capability desc")],
        endpoint="http://localhost:8000/a2a",
        health_endpoint="http://localhost:8000/health",
        metrics_endpoint="http://localhost:8000/metrics",
        tags=["test"],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_registry():
    registry = AgentRegistry()
    card = make_agent_card()

    await registry.register(card)
    assert await registry.is_registered(card.agent_id) is True
    assert await registry.get(card.agent_id) == card
    assert len(registry) == 1

    by_cap = await registry.find_by_capability("search")
    assert len(by_cap) == 1
    assert by_cap[0].agent_id == card.agent_id

    assert await registry.deregister(card.agent_id) is True
    assert await registry.is_registered(card.agent_id) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_registry():
    registry = ToolRegistry()
    tool_def = ToolDefinition(
        name="tool-1",
        description="desc",
        input_schema={},
        output_schema={},
    )
    await registry.register(tool_def)
    assert await registry.get("tool-1") == tool_def

    await registry.disable("tool-1")
    enabled_tools = await registry.get_all(enabled_only=True)
    assert len(enabled_tools) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capability_registry():
    registry = CapabilityRegistry()
    await registry.register_capability("search", AgentId("agent-1"))

    agents = await registry.get_agents_for_capability("search")
    assert agents == ["agent-1"]
    assert await registry.has_capability("search") is True
