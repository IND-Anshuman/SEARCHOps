"""
A2A Handshake and Agent Lifecycle Manager.
"""

from __future__ import annotations

import structlog

from searchops.a2a.protocol.envelopes import AgentHandshakeRequest, AgentHandshakeResponse
from searchops.core.interfaces.agent import AgentCapability, AgentCard
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.typing.aliases import AgentId

log = structlog.get_logger(__name__)


class A2AHandshakeManager:
    """Manages agent registration, handshakes, and heartbeat monitoring."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        capability_registry: CapabilityRegistry,
    ) -> None:
        self.agent_registry = agent_registry
        self.capability_registry = capability_registry

    async def handle_handshake(self, request: AgentHandshakeRequest) -> AgentHandshakeResponse:
        """Process incoming handshake request from a remote agent."""
        capabilities = [AgentCapability(name=c, description=f"Capability {c}") for c in request.capabilities]

        card = AgentCard(
            agent_id=request.agent_id,
            name=request.name,
            description=f"Remote agent {request.name}",
            version=request.version,
            capabilities=capabilities,
            endpoint=request.endpoint,
            health_endpoint=f"{request.endpoint}/health",
            metrics_endpoint=f"{request.endpoint}/metrics",
        )

        await self.agent_registry.register(card)

        for cap in request.capabilities:
            await self.capability_registry.register_capability(cap, request.agent_id)

        log.info("A2A Handshake successful", agent_id=request.agent_id, name=request.name)

        return AgentHandshakeResponse(
            success=True,
            message=f"Agent '{request.name}' registered successfully",
        )
