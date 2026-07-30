"""
ExecutionContext — carries all cross-cutting metadata for a single
platform execution (LangGraph run, agent task, or API request).

Used as the primary carrier for:
- Correlation/trace IDs (observability)
- Budget tracking (token + cost)
- Deadline enforcement
- User principal
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import shortuuid

from searchops.typing.aliases import CorrelationId, ExecutionId, TraceId, UserId
from searchops.typing.newtypes import CostUSD, DurationSeconds, TokenCount


@dataclass(slots=True)
class BudgetTracker:
    """Tracks token and cost consumption against configured limits."""
    
    max_tokens: int
    max_cost_usd: float
    consumed_tokens: int = 0
    consumed_cost_usd: float = 0.0
    
    def record_usage(self, tokens: int, cost_usd: float) -> None:
        """Record LLM usage against the budget."""
        self.consumed_tokens += tokens
        self.consumed_cost_usd += cost_usd
    
    @property
    def is_tokens_exceeded(self) -> bool:
        """True if token budget is exhausted."""
        return self.consumed_tokens >= self.max_tokens
    
    @property
    def is_cost_exceeded(self) -> bool:
        """True if cost budget is exhausted."""
        return self.consumed_cost_usd >= self.max_cost_usd
    
    @property
    def is_exceeded(self) -> bool:
        """True if either budget is exhausted."""
        return self.is_tokens_exceeded or self.is_cost_exceeded
    
    @property
    def remaining_tokens(self) -> int:
        """Remaining token budget."""
        return max(0, self.max_tokens - self.consumed_tokens)
    
    @property
    def remaining_cost_usd(self) -> float:
        """Remaining cost budget in USD."""
        return max(0.0, self.max_cost_usd - self.consumed_cost_usd)


@dataclass(slots=True)
class ExecutionContext:
    """Immutable execution context passed through the entire call stack.
    
    An ExecutionContext is created once per logical execution unit
    (API request, LangGraph run, agent task) and propagated without mutation.
    
    Attributes:
        execution_id: Unique identifier for this execution.
        correlation_id: Cross-service tracing correlation.
        trace_id: OpenTelemetry trace ID.
        user_id: Authenticated user (None for system-initiated executions).
        budget: Mutable budget tracker attached to this execution.
        deadline: Unix timestamp when this execution must be completed.
        metadata: Arbitrary execution metadata.
    """
    
    execution_id: ExecutionId = field(default_factory=lambda: ExecutionId(shortuuid.uuid()))
    correlation_id: CorrelationId = field(default_factory=lambda: CorrelationId(shortuuid.uuid()))
    trace_id: TraceId = field(default_factory=lambda: TraceId(""))
    user_id: UserId | None = None
    budget: BudgetTracker = field(
        default_factory=lambda: BudgetTracker(max_tokens=100_000, max_cost_usd=10.0)
    )
    deadline: float = field(
        default_factory=lambda: time.monotonic() + 600.0
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def create(
        cls,
        *,
        correlation_id: CorrelationId | None = None,
        user_id: UserId | None = None,
        max_tokens: int = 100_000,
        max_cost_usd: float = 10.0,
        timeout_seconds: float = 600.0,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        """Factory method for creating a new ExecutionContext."""
        return cls(
            correlation_id=correlation_id or CorrelationId(shortuuid.uuid()),
            user_id=user_id,
            budget=BudgetTracker(
                max_tokens=max_tokens,
                max_cost_usd=max_cost_usd,
            ),
            deadline=time.monotonic() + timeout_seconds,
            metadata=metadata or {},
        )
    
    @property
    def is_expired(self) -> bool:
        """True if the execution deadline has passed."""
        return time.monotonic() > self.deadline
    
    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining until the execution deadline."""
        return max(0.0, self.deadline - time.monotonic())
    
    def to_log_dict(self) -> dict[str, Any]:
        """Return a dict suitable for structured logging."""
        return {
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "consumed_tokens": self.budget.consumed_tokens,
            "consumed_cost_usd": self.budget.consumed_cost_usd,
            "remaining_seconds": self.remaining_seconds,
        }
