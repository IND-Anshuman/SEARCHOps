"""
Health check response schemas.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from searchops.shared.contracts.base import BaseSchema


class HealthStatusValue(enum.StrEnum):
    """Health check result status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseSchema):
    """Health status of a single platform component."""
    
    name: str
    status: HealthStatusValue
    message: str | None = None
    duration_ms: float
    details: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseSchema):
    """Overall platform health response."""
    
    status: HealthStatusValue
    service: str
    version: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    components: list[ComponentHealth] = Field(default_factory=list)
    uptime_seconds: float = 0.0
    
    @classmethod
    def healthy(cls, service: str, version: str, components: list[ComponentHealth], uptime: float) -> HealthResponse:
        """Factory for a healthy response."""
        return cls(
            status=HealthStatusValue.HEALTHY,
            service=service,
            version=version,
            components=components,
            uptime_seconds=uptime,
        )
    
    @classmethod
    def degraded(cls, service: str, version: str, components: list[ComponentHealth], uptime: float) -> HealthResponse:
        """Factory for a degraded response."""
        return cls(
            status=HealthStatusValue.DEGRADED,
            service=service,
            version=version,
            components=components,
            uptime_seconds=uptime,
        )
