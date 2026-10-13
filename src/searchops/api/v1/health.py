"""
Health check endpoints.

Endpoints:
  GET /health           — Overall platform health (all components)
  GET /health/live      — Kubernetes liveness probe (is the process running?)
  GET /health/ready     — Kubernetes readiness probe (can we serve traffic?)
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from searchops.bootstrap.container import ApplicationContainer, get_container
from searchops.bootstrap.startup import get_uptime_seconds
from searchops.config.settings import Settings, get_settings
from searchops.shared.contracts.health import HealthResponse, HealthStatusValue
from searchops.shared.contracts.health_probes import collect_component_health, overall_status

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
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> HealthResponse:
    """Overall platform health check.

    Probes every component the container actually built (Redis, Postgres,
    Qdrant, Neo4j) with a hard per-probe timeout. Components that were never
    constructed are reported as ``configured: false`` rather than omitted, so
    the response always describes the whole platform.

    The reported status is what was observed — never a default. A dependency
    that is down surfaces here instead of showing up as a mysteriously broken
    research job.
    """
    components = await collect_component_health(container)
    status = overall_status(components)

    if status != HealthStatusValue.HEALTHY:
        unhealthy = [c.name for c in components if c.status == HealthStatusValue.UNHEALTHY]
        log.warning("health.degraded", status=status.value, unhealthy=unhealthy)

    return HealthResponse(
        status=status,
        service=settings.app_name,
        version=settings.app_version,
        components=components,
        uptime_seconds=round(get_uptime_seconds(), 2),
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
async def readiness(
    container: Annotated[ApplicationContainer, Depends(_get_container)],
) -> JSONResponse:
    """Kubernetes readiness probe.

    Ready means the *required* dependencies answer. An unconfigured optional
    dependency does not make the service unready — but a configured one that is
    down does, so traffic stops being routed to a pod that cannot serve it.
    """
    components = await collect_component_health(container)
    unhealthy = [
        c.name for c in components if c.status == HealthStatusValue.UNHEALTHY and c.details.get("configured", True)
    ]

    if unhealthy:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "unhealthy": unhealthy},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
