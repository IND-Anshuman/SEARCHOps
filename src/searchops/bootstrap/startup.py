"""
Startup sequence.

Called once when the FastAPI application starts. Orchestrates:
1. Logging configuration
2. Observability (OTel)
3. Redis connection health check
4. DI container creation (with all singletons)

Design: Startup is SEQUENTIAL. Each step is isolated so failures are
easily traceable. If Redis is unavailable, the server refuses to start
rather than running in a degraded silent-failure state.
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
        The initialized ApplicationContainer with all singletons wired.

    Raises:
        RuntimeError: If any critical startup step fails (e.g. Redis unreachable).
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

    # Step 3: Redis health check (fail fast — without Redis, no job state is possible)
    await _verify_redis_connection(settings)

    # Step 4: DI Container — creates all singletons, wires dependencies
    container = ApplicationContainer.create()
    _set_container(container)

    elapsed = time.monotonic() - _startup_time
    log.info(
        "SEARCHOps platform started",
        startup_duration_seconds=round(elapsed, 3),
        env=settings.env,
    )

    return container


async def _verify_redis_connection(settings: object) -> None:
    """Ping Redis and raise if unreachable.

    This gives a clear startup error instead of silent cache misses at runtime.
    """
    from searchops.infrastructure.cache.redis import get_redis_client

    try:
        client = get_redis_client()
        await client.ping()
        log.info("Redis connection verified")
    except Exception as exc:
        log.error(
            "Redis connection failed — cannot start without Redis",
            error=str(exc),
        )
        raise RuntimeError(
            f"Cannot start SEARCHOps: Redis is unreachable. "
            f"Check REDIS_HOST / REDIS_PORT in .env. Error: {exc}"
        ) from exc


def get_uptime_seconds() -> float:
    """Return the number of seconds since startup."""
    if _startup_time == 0.0:
        return 0.0
    return time.monotonic() - _startup_time
