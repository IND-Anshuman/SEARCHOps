"""
Tool Registry for MCP tools.

Every MCP tool wrapper registers itself here. This enables:
- Dynamic tool discovery by agents
- Capability-based routing
- Runtime tool enable/disable via feature flags
"""
from __future__ import annotations

import asyncio
from typing import Any, final

import structlog

log = structlog.get_logger(__name__)


class ToolDefinition:
    """Metadata for a registered MCP tool."""
    
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        tags: list[str] | None = None,
        enabled: bool = True,
        version: str = "1.0.0",
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.tags: list[str] = tags or []
        self.enabled = enabled
        self.version = version
    
    def __repr__(self) -> str:
        return f"ToolDefinition(name={self.name!r}, version={self.version!r})"


@final
class ToolRegistry:
    """Registry for all MCP tool definitions."""
    
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._lock = asyncio.Lock()
    
    async def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition."""
        async with self._lock:
            self._tools[tool.name] = tool
            log.debug("Tool registered", tool_name=tool.name, version=tool.version)
    
    async def get(self, name: str) -> ToolDefinition | None:
        """Get a tool definition by name."""
        return self._tools.get(name)
    
    async def get_all(self, *, enabled_only: bool = True) -> list[ToolDefinition]:
        """Return all registered tool definitions."""
        tools = list(self._tools.values())
        if enabled_only:
            tools = [t for t in tools if t.enabled]
        return tools
    
    async def find_by_tag(self, tag: str) -> list[ToolDefinition]:
        """Find tools with a specific tag."""
        return [
            t for t in self._tools.values()
            if tag in t.tags and t.enabled
        ]
    
    async def enable(self, name: str) -> bool:
        """Enable a tool. Returns False if not found."""
        async with self._lock:
            if name not in self._tools:
                return False
            self._tools[name].enabled = True
            return True
    
    async def disable(self, name: str) -> bool:
        """Disable a tool. Returns False if not found."""
        async with self._lock:
            if name not in self._tools:
                return False
            self._tools[name].enabled = False
            return True
