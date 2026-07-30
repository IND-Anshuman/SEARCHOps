"""
AgentContext — metadata for a single A2A agent task execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from searchops.core.context.execution import ExecutionContext
from searchops.typing.aliases import AgentId, TaskId


@dataclass(slots=True)
class AgentContext:
    """Context for a single agent task execution."""
    
    execution_context: ExecutionContext
    agent_id: AgentId
    task_id: TaskId
    capability: str
    recursion_depth: int = 0
    max_recursion_depth: int = 25
    tool_calls_made: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_recursion_limit_reached(self) -> bool:
        """True if max recursion depth has been reached."""
        return self.recursion_depth >= self.max_recursion_depth
    
    def increment_depth(self) -> AgentContext:
        """Return a new AgentContext with recursion depth incremented by one."""
        return AgentContext(
            execution_context=self.execution_context,
            agent_id=self.agent_id,
            task_id=self.task_id,
            capability=self.capability,
            recursion_depth=self.recursion_depth + 1,
            max_recursion_depth=self.max_recursion_depth,
            tool_calls_made=self.tool_calls_made,
            metadata=self.metadata.copy(),
        )
