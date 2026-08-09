"""
Graceful shutdown sequence.

Called when the FastAPI application receives a shutdown signal.
Each resource is closed in reverse initialization order.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


async def shutdown() -> None:
    """Execute the graceful shutdown sequence.

    Closes all resources in reverse initialization order:
    1. Close Redis client connection pool
    2. Flush OTel spans (must be last so all other steps are traced)

    Each step is wrapped in try/except so one failure does not prevent
    the remaining resources from being cleaned up.
    """
    log.info("Initiating graceful shutdown")

    # Step 1: Close Redis (flushes pending pub/sub subscribers cleanly)
    await _close_redis()

    # Step 2: Flush OTel spans
    _flush_otel()

    log.info("Graceful shutdown complete")


async def _close_redis() -> None:
    """Close the global Redis async client."""
    try:
        from searchops.infrastructure.cache.redis import close_redis
        await close_redis()
    except Exception as exc:
        log.warning("Failed to close Redis connection", error=str(exc))


def _flush_otel() -> None:
    """Flush pending OpenTelemetry spans to the exporter."""
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
    except Exception as exc:
        log.warning("Failed to flush OTel spans", error=str(exc))
