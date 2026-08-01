"""
Agent-to-Agent Message Router.

Routes incoming and outgoing A2A message envelopes:
- Queries CapabilityRegistry / AgentRegistry to discover target endpoints
- Uses HTTPA2ATransport for remote agents
- Dispatches locally if recipient agent is in the same process
"""

from __future__ import annotations

import structlog

from searchops.a2a.protocol.envelopes import A2AMessageEnvelope, A2AMessageType
from searchops.a2a.transports.http import HTTPA2ATransport, IA2ATransport
from searchops.core.exceptions.application import UseCaseError
from searchops.core.exceptions.domain import EntityNotFoundError
from searchops.platform.registry.agent_registry import AgentRegistry
from searchops.platform.registry.capability_registry import CapabilityRegistry
from searchops.typing.aliases import AgentId

log = structlog.get_logger(__name__)


class A2AMessageRouter:
    """Central router for Agent-to-Agent message delivery."""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        capability_registry: CapabilityRegistry,
        transport: IA2ATransport | None = None,
        dlq: Any | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.capability_registry = capability_registry
        self.transport = transport or HTTPA2ATransport()
        self.dlq = dlq
        self._local_handlers: dict[AgentId, Any] = {}

    def register_local_agent(self, agent_id: AgentId, handler: Any) -> None:
        """Register a local in-process agent instance for direct invocation."""
        self._local_handlers[agent_id] = handler
        log.info("Registered local agent in router", agent_id=agent_id)

    async def route(self, envelope: A2AMessageEnvelope) -> A2AMessageEnvelope:
        """Route message envelope to recipient agent (local or remote)."""
        recipient_id = envelope.recipient_id

        try:
            # 1. Local execution
            if recipient_id in self._local_handlers:
                log.debug("Routing A2A message locally", recipient_id=recipient_id)
                handler = self._local_handlers[recipient_id]
                if hasattr(handler, "handle_a2a_message"):
                    return await handler.handle_a2a_message(envelope)
                raise UseCaseError(f"Local agent {recipient_id} does not support A2A message handling")

            # 2. Remote execution via Registry lookup
            card = await self.agent_registry.get(recipient_id)
            if card is None:
                raise EntityNotFoundError("AgentCard", recipient_id)

            log.debug("Routing A2A message remotely", recipient_id=recipient_id, endpoint=card.endpoint)
            return await self.transport.send(card.endpoint, envelope)

        except Exception as exc:
            log.error("Failed to route A2A message", recipient_id=recipient_id, error=str(exc))
            if self.dlq:
                await self.dlq.push_failed_envelope(envelope, reason=str(exc))
            raise

    async def route_by_capability(
        self,
        capability: str,
        envelope: A2AMessageEnvelope,
    ) -> A2AMessageEnvelope:
        """Find an agent providing capability and route message to it."""
        agents = await self.capability_registry.get_agents_for_capability(capability)
        if not agents:
            raise UseCaseError(f"No registered agents found for capability '{capability}'")

        # Pick first available agent for now (Round-Robin / Load balancing added in Phase 7)
        target_agent_id = agents[0]
        envelope.recipient_id = target_agent_id
        return await self.route(envelope)
