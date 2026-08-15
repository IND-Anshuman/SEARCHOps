"""
Capability Registry.

Maps capability names to the agents that expose them.
Used by the Planner Agent for task routing.
"""
from __future__ import annotations

import asyncio
from typing import final

import structlog

from searchops.typing.aliases import AgentId

log = structlog.get_logger(__name__)


@final
class CapabilityRegistry:
    """Maps capability names to agent IDs."""
    
    def __init__(self) -> None:
        # capability_name -> list of agent IDs that provide it
        self._capabilities: dict[str, list[AgentId]] = {}
        self._lock = asyncio.Lock()
    
    async def register_capability(
        self, capability_name: str, agent_id: AgentId
    ) -> None:
        """Register that an agent provides a capability."""
        async with self._lock:
            if capability_name not in self._capabilities:
                self._capabilities[capability_name] = []
            if agent_id not in self._capabilities[capability_name]:
                self._capabilities[capability_name].append(agent_id)
                log.debug(
                    "Capability registered",
                    capability=capability_name,
                    agent_id=agent_id,
                )
    
    async def deregister_agent(self, agent_id: AgentId) -> None:
        """Remove all capability registrations for an agent."""
        async with self._lock:
            for capability in self._capabilities:
                if agent_id in self._capabilities[capability]:
                    self._capabilities[capability].remove(agent_id)
    
    async def get_agents_for_capability(self, capability_name: str) -> list[AgentId]:
        """Return all agent IDs that provide the given capability."""
        return list(self._capabilities.get(capability_name, []))
    
    async def get_all_capabilities(self) -> list[str]:
        """Return all registered capability names."""
        return list(self._capabilities.keys())
    
    async def has_capability(self, capability_name: str) -> bool:
        """Return True if any agent provides the given capability."""
        agents = self._capabilities.get(capability_name, [])
        return len(agents) > 0
