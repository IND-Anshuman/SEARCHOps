"""
Health check protocol and value objects.
"""
from __future__ import annotations

import enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class HealthStatus(enum.StrEnum):
    """Health check result status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthCheckResult(BaseModel):
    """Result of a single health check."""
    
    name: str = Field(description="Health check name")
    status: HealthStatus
    message: str | None = Field(default=None, description="Optional detail message")
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = Field(description="Time taken for the check in milliseconds")


@runtime_checkable
class IHealthCheck(Protocol):
    """Contract for all health checks."""
    
    @property
    def name(self) -> str:
        """Unique health check name."""
        ...
    
    async def check(self) -> HealthCheckResult:
        """Execute the health check and return a result."""
        ...
