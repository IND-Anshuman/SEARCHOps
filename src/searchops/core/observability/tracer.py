"""
OpenTelemetry tracer provider setup.

Design decisions:
- OTLP gRPC exporter is the default (binary protocol, efficient for high-throughput)
- HTTP/protobuf fallback for environments where gRPC is blocked
- BatchSpanProcessor (not SimpleSpanProcessor) for production performance
- Resource attributes follow OTel semantic conventions

This module is called ONCE during bootstrap. All other modules call
``get_tracer(__name__)`` and never touch the provider directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

log = structlog.get_logger(__name__)

_provider: TracerProvider | None = None


def setup_tracer_provider(
    *,
    service_name: str,
    service_version: str,
    otlp_endpoint: str,
    otlp_protocol: str = "grpc",
    sample_rate: float = 1.0,
    enabled: bool = True,
) -> None:
    """Initialize and register the global OTel TracerProvider.

    Must be called before any ``get_tracer()`` calls. Calling it
    more than once is a no-op (guarded by the global ``_provider`` check).

    Args:
        service_name: OTel ``service.name`` resource attribute.
        service_version: OTel ``service.version`` resource attribute.
        otlp_endpoint: OTLP exporter endpoint (gRPC or HTTP).
        otlp_protocol: 'grpc' or 'http/protobuf'.
        sample_rate: Probability-based sampling rate [0.0, 1.0].
        enabled: If False, uses a no-op provider (useful in testing).
    """
    global _provider

    if _provider is not None:
        return

    if not enabled:
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
        from opentelemetry import trace

        _provider = SDKTracerProvider()
        trace.set_tracer_provider(_provider)
        log.info("OTel tracing disabled — using NoOp provider")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased, ParentBased

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: service_version,
                "deployment.environment": "production",
            }
        )

        sampler = ParentBased(root=TraceIdRatioBased(sample_rate))
        provider = SDKTracerProvider(resource=resource, sampler=sampler)

        # ── Exporter selection ────────────────────────────────────────────
        if otlp_protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)

        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        _provider = provider

        log.info(
            "OTel tracer provider initialized",
            service=service_name,
            endpoint=otlp_endpoint,
            protocol=otlp_protocol,
            sample_rate=sample_rate,
        )

    except Exception as exc:
        log.warning(
            "Failed to initialize OTel tracer provider — falling back to NoOp",
            error=str(exc),
        )
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
        from opentelemetry import trace

        _provider = SDKTracerProvider()
        trace.set_tracer_provider(_provider)


def get_tracer(name: str) -> "opentelemetry.trace.Tracer":  # type: ignore[name-defined]
    """Return a tracer for the given instrumentation scope.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        An OTel Tracer. If the provider hasn't been set up, returns a NoOp tracer.
    """
    from opentelemetry import trace

    return trace.get_tracer(name)


def setup_auto_instrumentation() -> None:
    """Enable automatic instrumentation for known libraries.

    Called once from bootstrap after the tracer provider is initialized.
    Each instrumentor is guarded by a try/except to prevent startup failure
    if a library version is incompatible.
    """
    _try_instrument("FastAPI", _instrument_fastapi)
    _try_instrument("HTTPX", _instrument_httpx)
    _try_instrument("SQLAlchemy", _instrument_sqlalchemy)
    _try_instrument("Redis", _instrument_redis)
    _try_instrument("AsyncPG", _instrument_asyncpg)


def _try_instrument(name: str, fn: "Callable[[], None]") -> None:  # type: ignore[name-defined]
    try:
        fn()
        log.debug("Auto-instrumented", library=name)
    except Exception as exc:
        log.warning("Auto-instrumentation failed", library=name, error=str(exc))


def _instrument_fastapi() -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor().instrument()


def _instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    HTTPXClientInstrumentor().instrument()


def _instrument_sqlalchemy() -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    SQLAlchemyInstrumentor().instrument()


def _instrument_redis() -> None:
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    RedisInstrumentor().instrument()


def _instrument_asyncpg() -> None:
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    AsyncPGInstrumentor().instrument()
