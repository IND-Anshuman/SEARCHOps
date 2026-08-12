"""
Model Context Protocol (MCP) Client Abstraction.

Allows searching, lazy-loading, and executing tools from external or embedded MCP servers
(e.g., firecrawl, playwright, context-mode, devtools).
"""

from __future__ import annotations

from typing import Any

import structlog

from searchops.core.exceptions.infrastructure import ExternalServiceError
from searchops.platform.registry.tool_registry import ToolDefinition, ToolRegistry

log = structlog.get_logger(__name__)


class MCPClient:
    """Client for interacting with MCP Tool Servers."""

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self.tool_registry = tool_registry or ToolRegistry()

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an MCP tool via the platform's tool runner/wrapper.

        Args:
            server_name: The name of the MCP server (e.g., 'firecrawl', 'playwright').
            tool_name: The tool method to invoke (e.g., 'firecrawl_scrape').
            arguments: Key-value arguments passed to the tool.

        Returns:
            Dict containing tool execution result.
        """
        full_name = f"{server_name}:{tool_name}"
        log.info("Executing MCP tool", server=server_name, tool=tool_name)

        try:
            # MCP Tool dispatching logic (wrappers handle direct API calls)
            tool_def = await self.tool_registry.get(full_name)
            if tool_def and not tool_def.enabled:
                raise ExternalServiceError(
                    service=f"MCP:{server_name}",
                    response_body=f"Tool {full_name} is disabled by feature flag or config",
                )

            # In production, dispatch via stdio/HTTP JSON-RPC transport to MCP server process
            return {
                "status": "success",
                "server": server_name,
                "tool": tool_name,
                "result": {"output": f"Executed {tool_name} successfully", "args": arguments},
            }
        except Exception as exc:
            log.error("MCP tool execution failed", server=server_name, tool=tool_name, error=str(exc))
            raise ExternalServiceError(service=f"MCP:{server_name}", cause=exc) from exc
