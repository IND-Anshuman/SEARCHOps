"""
Unit tests for agent, capability, and tool platform registries.
"""

from __future__ import annotations

import pytest
from searchops.core.interfaces.agent import AgentCard, AgentCapability
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.platform.registry.tool_registry import ToolRegistry, ToolDefinition
from searchops.typing.aliases import AgentId


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_registry_lifecycle():
    registry = AgentRegistry()

    cap = AgentCapability(name="web_search", description="Search web", input_schema={}, output_schema={})
    card = AgentCard(
        agent_id=AgentId("search_agent_1"),
        name="SearchAgent",
        description="Executes search queries",
        version="1.0.0",
        endpoint="http://localhost:8000/a2a",
        health_endpoint="http://localhost:8000/health",
        metrics_endpoint="http://localhost:8000/metrics",
        capabilities=[cap],
        tags=["search"],
    )

    await registry.register(card)
    assert len(registry) == 1
    assert await registry.is_registered(AgentId("search_agent_1"))
    assert await registry.get(AgentId("search_agent_1")) == card

    found_by_cap = await registry.find_by_capability("web_search")
    assert len(found_by_cap) == 1

    found_by_tag = await registry.find_by_tag("search")
    assert len(found_by_tag) == 1

    assert await registry.heartbeat(AgentId("search_agent_1"))
    assert await registry.deregister(AgentId("search_agent_1"))
    assert len(registry) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capability_registry_lifecycle():
    cap_reg = CapabilityRegistry()
    await cap_reg.register_capability("search", AgentId("agent_1"))
    assert await cap_reg.has_capability("search")
    assert AgentId("agent_1") in await cap_reg.get_agents_for_capability("search")
    assert "search" in await cap_reg.get_all_capabilities()

    await cap_reg.deregister_agent(AgentId("agent_1"))
    assert len(await cap_reg.get_agents_for_capability("search")) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_registry_lifecycle():
    tool_reg = ToolRegistry()
    tool = ToolDefinition(
        name="firecrawl_scrape",
        description="Scrape web pages",
        input_schema={},
        output_schema={},
        tags=["scraper"],
    )

    await tool_reg.register(tool)
    assert await tool_reg.get("firecrawl_scrape") == tool
    assert len(await tool_reg.get_all()) == 1
    assert len(await tool_reg.find_by_tag("scraper")) == 1

    assert await tool_reg.disable("firecrawl_scrape")
    assert len(await tool_reg.get_all(enabled_only=True)) == 0

    assert await tool_reg.enable("firecrawl_scrape")
    assert len(await tool_reg.get_all(enabled_only=True)) == 1
