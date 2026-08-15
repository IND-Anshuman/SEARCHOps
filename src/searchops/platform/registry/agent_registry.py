"""
Agent Registry.

A thread-safe, async registry for all A2A agents in the platform.
The Planner Agent queries this registry to discover what agents are available
and what capabilities they expose.

Registrations are stored in-memory (primary) and optionally persisted to Redis.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import final

import structlog

from searchops.core.interfaces.agent import AgentCard
from searchops.typing.aliases import AgentId

log = structlog.get_logger(__name__)


@final
class AgentRegistry:
    """Central registry for all platform agents.
    
    Agents register their AgentCard on startup. The registry maintains
    last-heartbeat timestamps to detect unhealthy agents.
    
    Thread-safe: uses asyncio.Lock for all mutations.
    """
    
    def __init__(self) -> None:
        self._agents: dict[AgentId, AgentCard] = {}
        self._heartbeats: dict[AgentId, datetime] = {}
        self._lock = asyncio.Lock()
    
    async def register(self, agent_card: AgentCard) -> None:
        """Register an agent. Overwrites any existing registration for the same ID."""
        async with self._lock:
            self._agents[agent_card.agent_id] = agent_card
            self._heartbeats[agent_card.agent_id] = datetime.now(timezone.utc)
            log.info(
                "Agent registered",
                agent_id=agent_card.agent_id,
                agent_name=agent_card.name,
                capabilities=[c.name for c in agent_card.capabilities],
            )
    
    async def deregister(self, agent_id: AgentId) -> bool:
        """Remove an agent from the registry. Returns True if it was registered."""
        async with self._lock:
            if agent_id not in self._agents:
                return False
            del self._agents[agent_id]
            self._heartbeats.pop(agent_id, None)
            log.info("Agent deregistered", agent_id=agent_id)
            return True
    
    async def heartbeat(self, agent_id: AgentId) -> bool:
        """Record a heartbeat for an agent. Returns False if agent is not registered."""
        async with self._lock:
            if agent_id not in self._agents:
                return False
            self._heartbeats[agent_id] = datetime.now(timezone.utc)
            return True
    
    async def get(self, agent_id: AgentId) -> AgentCard | None:
        """Get an agent's card by ID."""
        return self._agents.get(agent_id)
    
    async def get_all(self) -> list[AgentCard]:
        """Return all registered agent cards."""
        return list(self._agents.values())
    
    async def find_by_capability(self, capability_name: str) -> list[AgentCard]:
        """Find all agents that expose a specific capability."""
        return [
            card
            for card in self._agents.values()
            if any(c.name == capability_name for c in card.capabilities)
        ]
    
    async def find_by_tag(self, tag: str) -> list[AgentCard]:
        """Find all agents with a specific tag."""
        return [
            card
            for card in self._agents.values()
            if tag in card.tags
        ]
    
    async def is_registered(self, agent_id: AgentId) -> bool:
        """Check if an agent is registered."""
        return agent_id in self._agents
    
    async def get_stale_agents(
        self, max_silence_seconds: float = 60.0
    ) -> list[AgentId]:
        """Return IDs of agents that have not sent a heartbeat within the threshold."""
        now = datetime.now(timezone.utc)
        stale: list[AgentId] = []
        for agent_id, last_beat in self._heartbeats.items():
            silence = (now - last_beat).total_seconds()
            if silence > max_silence_seconds:
                stale.append(agent_id)
        return stale
    
    def __len__(self) -> int:
        return len(self._agents)
