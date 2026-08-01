"""
Agent-to-Agent (A2A) Protocol Envelopes and JSON-RPC 2.0 Specification.

Follows the A2A messaging protocol format for inter-agent communication:
- JsonRpcRequest / JsonRpcResponse
- A2AMessageEnvelope (wraps request/response with agent routing metadata)
- AgentHandshakeRequest / AgentHandshakeResponse
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from searchops.shared.contracts.base import BaseSchema
from searchops.typing.aliases import AgentId, CorrelationId, TaskId

T = TypeVar("T")


class A2AMessageType(enum.StrEnum):
    """Types of messages exchanged over the A2A protocol."""

    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"
    TASK_CANCEL = "task_cancel"
    HEARTBEAT = "heartbeat"
    HANDSHAKE = "handshake"
    EVENT_NOTIFICATION = "event_notification"


class JsonRpcRequest(BaseSchema):
    """JSON-RPC 2.0 Request Object."""

    jsonrpc: str = Field(default="2.0", frozen=True)
    method: str = Field(description="Remote agent capability/method name")
    params: dict[str, Any] = Field(default_factory=dict, description="Method arguments")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="RPC request ID")


class JsonRpcError(BaseSchema):
    """JSON-RPC 2.0 Error Object."""

    code: int
    message: str
    data: Any | None = None


class JsonRpcResponse(BaseSchema, Generic[T]):
    """JSON-RPC 2.0 Response Object."""

    jsonrpc: str = Field(default="2.0", frozen=True)
    result: T | None = None
    error: JsonRpcError | None = None
    id: str


class A2AMessageEnvelope(BaseSchema):
    """Outer message envelope for all A2A network transport."""

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message_type: A2AMessageType
    sender_id: AgentId
    recipient_id: AgentId
    task_id: TaskId | None = None
    correlation_id: CorrelationId = Field(default_factory=lambda: CorrelationId(str(uuid.uuid4())))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = Field(default=None, description="Cryptographic signature if security enabled")


class AgentHandshakeRequest(BaseSchema):
    """Agent handshake registration request."""

    agent_id: AgentId
    name: str
    version: str
    capabilities: list[str]
    endpoint: str


class AgentHandshakeResponse(BaseSchema):
    """Agent handshake registration response."""

    success: bool
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = "Registered successfully"
