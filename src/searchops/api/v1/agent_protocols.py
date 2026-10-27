"""Agent-facing protocol endpoints: MCP tool execution and A2A messaging.

Two protocol surfaces that previously existed only as library code:

- **MCP** (Model Context Protocol) — the tool registry and client could not
  be reached over HTTP, and the client fabricated a success payload for any
  tool name it was handed. It now dispatches to the real bound handler and
  reports an honest failure when there is nothing to call.
- **A2A** (agent-to-agent) — the router, handshake manager and dead-letter
  queue were implemented and tested but mounted nowhere, so no agent could
  ever reach them. They are reachable here.

Both surfaces are read/write, so both are behind the platform's API-key
middleware like every other route.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# These are runtime imports, not TYPE_CHECKING-only: FastAPI resolves the
# request-body model through the annotation at runtime when it builds the
# OpenAPI schema. Moving them into a TYPE_CHECKING block makes
# ``app.openapi()`` raise PydanticUserError.
from searchops.a2a.protocol.envelopes import A2AMessageEnvelope, AgentHandshakeRequest  # noqa: TC001
from searchops.bootstrap.container import ApplicationContainer, get_container
from searchops.core.exceptions.infrastructure import ExternalServiceError
from searchops.middleware.auth import require_service_principal

log = structlog.get_logger(__name__)

router = APIRouter(tags=["Agent Protocols"], dependencies=[Depends(require_service_principal)])


async def _get_container() -> ApplicationContainer:
    return get_container()


# ── MCP ──────────────────────────────────────────────────────────────────────


class MCPToolCall(BaseModel):
    """A request to execute one MCP tool."""

    server: str = Field(description="MCP server name, e.g. 'brightdata'.")
    tool: str = Field(description="Tool name, e.g. 'brightdata_serp_search'.")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the tool.")


@router.get(
    "/mcp/tools",
    summary="List the MCP tools this platform exposes",
)
async def list_mcp_tools(
    container: Annotated[ApplicationContainer, Depends(_get_container)],
    server: str | None = None,
) -> dict[str, Any]:
    """List registered MCP tools with their input schemas.

    A tool appears here only if it is registered, and a registered tool always
    has its handler bound — the list is a catalogue of things that can
    actually be called.
    """
    tools = await container.mcp_client.list_tools(server)
    return {"tools": tools, "count": len(tools)}


@router.post(
    "/mcp/call",
    summary="Execute an MCP tool",
)
async def call_mcp_tool(
    body: MCPToolCall,
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> dict[str, Any]:
    """Execute a registered MCP tool and return its real result.

    An unknown, disabled, or unbound tool returns 404/409/502 with the
    reason — never a synthetic "executed successfully" payload.
    """
    try:
        return await container.mcp_client.call_tool(
            body.server,
            body.tool,
            body.arguments,
        )
    except ExternalServiceError as exc:
        detail = getattr(exc, "response_body", None) or str(exc)
        log.warning("mcp.call.failed", server=body.server, tool=body.tool, detail=str(detail))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(detail),
        ) from exc


# ── A2A ──────────────────────────────────────────────────────────────────────


@router.get(
    "/a2a/agents",
    summary="List registered A2A agents",
)
async def list_agents(
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> dict[str, Any]:
    """List the agents that have completed a handshake with this platform."""
    cards = await container.agent_registry.get_all()
    return {
        "agents": [
            {
                "agent_id": str(card.agent_id),
                "name": card.name,
                "description": card.description,
                "version": card.version,
                "endpoint": card.endpoint,
                "capabilities": [str(getattr(c, "name", c)) for c in card.capabilities],
            }
            for card in cards
        ],
        "count": len(cards),
    }


@router.post(
    "/a2a/handshake",
    summary="Register a remote agent via the A2A handshake",
)
async def a2a_handshake(
    request: AgentHandshakeRequest,
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> dict[str, Any]:
    """Register a remote agent and its capabilities.

    This is the entry point that makes the A2A module reachable: without it
    the router had no way to learn about a peer agent.
    """
    response = await container.a2a_handshake.handle_handshake(request)
    return response.model_dump()


@router.post(
    "/a2a/message",
    summary="Route an A2A message envelope to a recipient agent",
)
async def a2a_message(
    envelope: A2AMessageEnvelope,
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> dict[str, Any]:
    """Route a message to a local or remote agent.

    A message that cannot be routed is pushed to the dead-letter queue and
    reported as a failure. The platform never invents a successful response
    for a message it did not deliver.
    """
    try:
        result = await container.a2a_router.route(envelope)
    except Exception as exc:
        log.warning("a2a.route.failed", recipient=str(envelope.recipient_id), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Message could not be routed: {exc}",
        ) from exc
    return result.model_dump()


@router.get(
    "/a2a/dlq",
    summary="Inspect the A2A dead-letter queue",
)
async def read_a2a_dlq(
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> dict[str, Any]:
    """Show the messages that failed to route.

    A failure that is invisible is a failure that gets retried forever.
    """
    dlq = container.a2a_router.dlq
    if dlq is None:
        return {"messages": [], "count": 0, "note": "No dead-letter queue is configured."}
    entries = await dlq.get_failed_envelopes() if hasattr(dlq, "get_failed_envelopes") else []
    return {"messages": entries, "count": len(entries)}
