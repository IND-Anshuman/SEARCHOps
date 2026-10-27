"""
Model Context Protocol (MCP) Client Abstraction.

Allows searching, lazy-loading, and executing tools from external or embedded MCP servers
(e.g., firecrawl, playwright, context-mode, devtools).
"""

from __future__ import annotations

from typing import Any

import structlog

from searchops.core.exceptions.infrastructure import ExternalServiceError
from searchops.platform.registry.tool_registry import ToolRegistry

log = structlog.get_logger(__name__)


class MCPClient:
    """Client for interacting with MCP Tool Servers."""

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self.tool_registry = tool_registry or ToolRegistry()

    async def list_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """List the tools the registry knows about.

        The registry holds tool *definitions* — metadata registered by the
        wrappers in ``searchops.mcp.brightdata_mcp_tools``. Listing them is a
        real operation and returns real data.
        """
        definitions = await self.tool_registry.get_all(enabled_only=True)
        if server_name is not None:
            prefix = f"{server_name}:"
            definitions = [d for d in definitions if d.name.startswith(prefix)]
        return [
            {
                "name": d.name,
                "description": d.description,
                "input_schema": d.input_schema,
                "output_schema": d.output_schema,
                "tags": list(d.tags),
                "version": d.version,
            }
            for d in definitions
        ]

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
            Dict containing the tool's real execution result.

        Raises:
            ExternalServiceError: when the tool is unknown, disabled, or the
                underlying wrapper failed. This method never reports a success
                it did not observe.
        """
        full_name = f"{server_name}:{tool_name}"
        log.info("Executing MCP tool", server=server_name, tool=tool_name)

        try:
            tool_def = await self.tool_registry.get(full_name)
            if tool_def is None:
                raise ExternalServiceError(
                    service=f"MCP:{server_name}",
                    response_body=(
                        f"Tool '{full_name}' is not registered. "
                        f"Registered tools: {sorted(t.name for t in await self.tool_registry.get_all(enabled_only=True))}"
                    ),
                )
            if not tool_def.enabled:
                raise ExternalServiceError(
                    service=f"MCP:{server_name}",
                    response_body=f"Tool {full_name} is disabled by feature flag or config",
                )

            # Dispatch to the real wrapper. The registry stores metadata; the
            # callable that actually performs the work is registered alongside
            # it by the wrapper module. If no callable is present, the tool
            # cannot be executed — saying otherwise would fabricate a result.
            handler = self.tool_registry.get_handler(full_name)
            if handler is None:
                raise ExternalServiceError(
                    service=f"MCP:{server_name}",
                    response_body=(
                        f"Tool '{full_name}' has no executable handler bound. "
                        "The registry holds its definition only, so there is "
                        "nothing to call — this is a configuration error, not a "
                        "successful execution."
                    ),
                )

            result = await handler(**arguments)
            return {
                "status": "success",
                "server": server_name,
                "tool": tool_name,
                "result": result,
            }
        except ExternalServiceError:
            raise
        except Exception as exc:
            log.error("MCP tool execution failed", server=server_name, tool=tool_name, error=str(exc))
            raise ExternalServiceError(service=f"MCP:{server_name}", cause=exc) from exc
