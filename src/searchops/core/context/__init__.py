"""Context objects — carry execution metadata through the call stack."""

from searchops.core.context.agent import AgentContext
from searchops.core.context.execution import ExecutionContext
from searchops.core.context.request import RequestContext
from searchops.core.context.research import ResearchContext
from searchops.core.context.vars import (
    current_correlation_id,
    current_execution_context,
    current_request_context,
    current_trace_id,
)

__all__ = [
    "AgentContext",
    "ExecutionContext",
    "RequestContext",
    "ResearchContext",
    "current_correlation_id",
    "current_execution_context",
    "current_request_context",
    "current_trace_id",
]
