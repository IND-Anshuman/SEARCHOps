"""
Startup sequence.

Called once when the FastAPI application starts. Orchestrates:
1. Logging configuration
2. Observability (OTel, Langfuse)
3. DI container creation
4. Infrastructure connections (Phase 2+)
5. Plugin loading (Phase 7+)
6. Agent registration (Phase 7+)

Design: Startup is SEQUENTIAL, not parallel. Each step is isolated
so failures are easily traceable.
"""
from __future__ import annotations

import time

import structlog

from searchops.bootstrap.container import ApplicationContainer, _set_container
from searchops.config.settings import get_settings
from searchops.core.logging.configure import configure_logging
from searchops.core.observability.tracer import (
    setup_auto_instrumentation,
    setup_tracer_provider,
)

log = structlog.get_logger(__name__)

_startup_time: float = 0.0


async def startup() -> ApplicationContainer:
    """Execute the complete application startup sequence.
    
    Returns:
        The initialized ApplicationContainer.
    
    Raises:
        RuntimeError: If any critical startup step fails.
    """
    global _startup_time
    _startup_time = time.monotonic()
    
    settings = get_settings()
    
    # Step 1: Logging (must be first — everything else logs)
    configure_logging(
        level=settings.log_level,
        format=settings.log_format,
        service_name=settings.app_name,
        service_version=settings.app_version,
    )
    
    log.info(
        "Starting SEARCHOps platform",
        env=settings.env,
        version=settings.app_version,
    )
    
    # Step 2: OpenTelemetry (before any async work)
    if settings.observability.enabled:
        setup_tracer_provider(
            service_name=settings.observability.service_name,
            service_version=settings.observability.service_version,
            otlp_endpoint=settings.observability.otlp_endpoint,
            otlp_protocol=settings.observability.otlp_protocol,
            sample_rate=settings.observability.sample_rate,
            enabled=settings.observability.traces_enabled,
        )
        setup_auto_instrumentation()
    
    # Step 3: DI Container
    container = ApplicationContainer.create()
    _set_container(container)
    
    elapsed = time.monotonic() - _startup_time
    log.info(
        "SEARCHOps platform started",
        startup_duration_seconds=round(elapsed, 3),
        env=settings.env,
    )
    
    return container


def get_uptime_seconds() -> float:
    """Return the number of seconds since startup."""
    if _startup_time == 0.0:
        return 0.0
    return time.monotonic() - _startup_time
