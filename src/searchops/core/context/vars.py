"""
ContextVar bindings for request-scoped data.

Using contextvars ensures thread/async safety without passing
context objects through every function signature.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from searchops.core.context.execution import ExecutionContext
    from searchops.core.context.request import RequestContext

# ─── ContextVar declarations ──────────────────────────────────────────────────

#: The ExecutionContext for the current async task.
current_execution_context: ContextVar[ExecutionContext | None] = ContextVar(
    "current_execution_context", default=None
)

#: The RequestContext for the current HTTP request.
current_request_context: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context", default=None
)

#: Shortcut: current correlation ID for structured logging.
current_correlation_id: ContextVar[str] = ContextVar(
    "current_correlation_id", default=""
)

#: Shortcut: current OpenTelemetry trace ID.
current_trace_id: ContextVar[str] = ContextVar(
    "current_trace_id", default=""
)
