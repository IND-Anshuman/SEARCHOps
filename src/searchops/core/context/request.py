"""
RequestContext — per-HTTP-request metadata injected by middleware.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from searchops.core.context.execution import ExecutionContext
from searchops.typing.aliases import CorrelationId, RequestId, UserId


@dataclass(slots=True)
class RequestContext:
    """Metadata for a single HTTP request.
    
    Created by RequestContextMiddleware and bound to contextvars
    so it's accessible throughout the request lifecycle without
    explicit parameter passing.
    """
    
    request_id: RequestId
    correlation_id: CorrelationId
    user_id: UserId | None = None
    path: str = ""
    method: str = ""
    client_ip: str | None = None
    user_agent: str | None = None
    execution_context: ExecutionContext | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_log_dict(self) -> dict[str, Any]:
        """Return a dict suitable for structlog context binding."""
        return {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "user_id": self.user_id,
            "path": self.path,
            "method": self.method,
            "client_ip": self.client_ip,
        }
