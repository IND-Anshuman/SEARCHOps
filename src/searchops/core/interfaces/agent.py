"""
Agent interface and associated value objects.

Every agent in the platform implements IAgent, exposing:
- capabilities (what the agent can do)
- an AgentCard (A2A protocol metadata)
- execution (handling A2A tasks)
- health (is the agent healthy?)
"""
from __future__ import annotations

import enum
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from searchops.typing.aliases import AgentId, TaskId


class AgentCapability(BaseModel):
    """A single capability advertised by an agent."""
    
    name: str = Field(description="Unique capability identifier")
    description: str = Field(description="Human-readable capability description")
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the input this capability accepts",
    )
    output_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the output this capability produces",
    )
    version: str = Field(default="1.0.0", description="Capability version")


class AgentCard(BaseModel):
    """A2A-compliant agent card for capability discovery."""
    
    agent_id: AgentId = Field(description="Unique agent identifier")
    name: str = Field(description="Human-readable agent name")
    description: str = Field(description="Agent purpose and capabilities")
    version: str = Field(description="Agent version (semver)")
    capabilities: list[AgentCapability] = Field(
        default_factory=list,
        description="List of agent capabilities",
    )
    endpoint: str = Field(description="A2A endpoint URL")
    health_endpoint: str = Field(description="Health check endpoint URL")
    metrics_endpoint: str = Field(description="Prometheus metrics endpoint URL")
    tags: list[str] = Field(default_factory=list, description="Searchable tags")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional agent metadata",
    )


class AgentTaskStatus(enum.StrEnum):
    """Lifecycle states of an agent task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_APPROVAL = "awaiting_approval"


class AgentTask(BaseModel):
    """An A2A task submitted to an agent."""
    
    task_id: TaskId
    capability: str
    input: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 300.0
    require_approval: bool = False


class AgentTaskResult(BaseModel):
    """The result of an executed A2A task."""
    
    task_id: TaskId
    status: AgentTaskStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class IAgent(Protocol):
    """Core agent protocol. Every agent in the platform implements this interface."""
    
    @property
    def agent_card(self) -> AgentCard:
        """Return the A2A-compliant agent card."""
        ...
    
    async def execute(self, task: AgentTask) -> AgentTaskResult:
        """Execute a task synchronously. Returns when the task is complete."""
        ...
    
    async def execute_streaming(
        self, task: AgentTask
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute a task with streaming updates. Yields progress events."""
        ...
    
    async def cancel(self, task_id: TaskId) -> bool:
        """Cancel a running task. Returns True if cancelled successfully."""
        ...
    
    async def health_check(self) -> bool:
        """Return True if the agent is healthy and ready to accept tasks."""
        ...
    
    async def initialize(self) -> None:
        """Perform async initialization."""
        ...
    
    async def shutdown(self) -> None:
        """Perform graceful shutdown."""
        ...
