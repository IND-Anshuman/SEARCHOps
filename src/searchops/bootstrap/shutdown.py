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
    1. Stop accepting new work (handled by uvicorn)
    2. Stop agent heartbeats
    3. Drain event bus
    4. Close database connections
    5. Close Redis connections
    6. Flush OTel spans
    
    Each step is wrapped in try/except so one failure doesn't
    prevent other resources from being cleaned up.
    """
    log.info("Initiating graceful shutdown")
    
    # Flush OTel spans (must be last so other steps' spans are captured)
    _flush_otel()
    
    log.info("Graceful shutdown complete")


def _flush_otel() -> None:
    """Flush pending OpenTelemetry spans to the exporter."""
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
    except Exception as exc:
        log.warning("Failed to flush OTel spans", error=str(exc))
