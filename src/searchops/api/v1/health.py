"""
Health check endpoints.

Endpoints:
  GET /health           — Overall platform health (all components)
  GET /health/live      — Kubernetes liveness probe (is the process running?)
  GET /health/ready     — Kubernetes readiness probe (can we serve traffic?)
"""
from __future__ import annotations

import time
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse

from searchops.bootstrap.container import ApplicationContainer, get_container
from searchops.bootstrap.startup import get_uptime_seconds
from searchops.config.settings import Settings, get_settings
from searchops.shared.contracts.health import ComponentHealth, HealthResponse, HealthStatusValue

log = structlog.get_logger(__name__)

router = APIRouter()


async def _get_container() -> ApplicationContainer:
    return get_container()


@router.get(
    "",
    response_model=HealthResponse,
    summary="Platform health",
    description="Returns the health status of all platform components.",
)
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Overall platform health check."""
    components: list[ComponentHealth] = []
    overall_status = HealthStatusValue.HEALTHY
    
    # In Phase 2+, we will check: database, redis, neo4j, qdrant
    # For Phase 1, we just return healthy.
    
    uptime = get_uptime_seconds()
    
    return HealthResponse.healthy(
        service=settings.app_name,
        version=settings.app_version,
        components=components,
        uptime=round(uptime, 2),
    )


@router.get(
    "/live",
    summary="Liveness probe",
    description="Kubernetes liveness probe. Returns 200 if the process is alive.",
)
async def liveness() -> dict[str, str]:
    """Kubernetes liveness probe."""
    return {"status": "alive"}


@router.get(
    "/ready",
    summary="Readiness probe",
    description="Kubernetes readiness probe. Returns 200 only if ready to serve traffic.",
)
async def readiness() -> dict[str, str]:
    """Kubernetes readiness probe."""
    # Phase 1: always ready. Phase 2+ will check infrastructure connections.
    return {"status": "ready"}
