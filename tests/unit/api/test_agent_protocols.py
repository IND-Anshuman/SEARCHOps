"""Tests for the agent-protocol endpoints (MCP tool execution, A2A messaging).

These surfaces existed as library code with no HTTP entry point, so nothing
could reach them. They are mounted now, and these tests pin the contract:
in particular, an MCP call for a tool that cannot be executed must fail
rather than report a fabricated success.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from searchops.api.v1 import agent_protocols
from searchops.api.v1.agent_protocols import router
from searchops.platform.registry.tool_registry import ToolDefinition, ToolRegistry


class _StubContainer:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry
        self.agent_registry = AsyncMock()
        self.agent_registry.get_all = AsyncMock(return_value=[])
        self.capability_registry = AsyncMock()
        self.a2a_router = AsyncMock()
        self.a2a_router.dlq = None
        self.a2a_handshake = AsyncMock()

        from searchops.mcp.client import MCPClient

        self.mcp_client = MCPClient(tool_registry=tool_registry)


def _app(registry: ToolRegistry) -> TestClient:
    app = FastAPI()
    container = _StubContainer(registry)
    app.dependency_overrides[agent_protocols._get_container] = lambda: container
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.mark.unit
def test_mcp_tools_endpoint_lists_registered_tools():
    registry = ToolRegistry()
    client = _app(registry)
    res = client.get("/api/v1/mcp/tools")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 0
    assert body["tools"] == []


@pytest.mark.unit
def test_mcp_call_refuses_an_unregistered_tool():
    """The critical contract: no fabricated success."""
    client = _app(ToolRegistry())
    res = client.post(
        "/api/v1/mcp/call",
        json={"server": "brightdata", "tool": "brightdata_serp_search", "arguments": {"query": "x"}},
    )
    assert res.status_code == 502
    assert "not registered" in res.json()["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mcp_call_executes_a_bound_handler():
    registry = ToolRegistry()

    async def _handler(**kwargs):
        return {"results": [{"title": "T", "url": "https://a.com"}]}

    await registry.register(
        ToolDefinition(
            name="brightdata:brightdata_serp_search",
            description="Search",
            input_schema={},
            output_schema={},
        ),
        handler=_handler,
    )

    client = _app(registry)
    res = client.post(
        "/api/v1/mcp/call",
        json={"server": "brightdata", "tool": "brightdata_serp_search", "arguments": {"query": "x"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["result"]["results"][0]["url"] == "https://a.com"


@pytest.mark.unit
def test_a2a_agents_endpoint_returns_the_registered_agents():
    client = _app(ToolRegistry())
    res = client.get("/api/v1/a2a/agents")
    assert res.status_code == 200
    assert res.json()["count"] == 0
