"""
structlog configuration for the SEARCHOps platform.

Design decisions:
- JSON format in production/staging (machine-parseable for log aggregators)
- Console format in development (human-readable)
- OTel correlation: trace_id and span_id are automatically injected via processor
- Request context (correlation_id, request_id) injected via ContextVar processor
- Sensitive fields are automatically redacted before emission

Never call logging.basicConfig() in application code. This module owns
the entire logging configuration. It is called ONCE during bootstrap.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

from searchops.core.logging.processors import (
    add_correlation_id,
    add_open_telemetry_ids,
    redact_sensitive_fields,
)


_CONFIGURED = False


def configure_logging(
    *,
    level: str = "INFO",
    format: str = "json",
    service_name: str = "searchops",
    service_version: str = "0.1.0",
) -> None:
    """Configure structlog and the stdlib logging bridge.

    This function is idempotent — calling it more than once is a no-op.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        format: Output format — 'json' or 'console'.
        service_name: Injected into every log record as 'service'.
        service_version: Injected into every log record as 'service_version'.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    # ── Shared processors (run for both console and JSON) ─────────────────
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_correlation_id,
        add_open_telemetry_ids,
        redact_sensitive_fields,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]

    # ── Renderer selection ────────────────────────────────────────────────
    if format == "console":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            # Must be last before the renderer
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── stdlib root logger (for third-party libs) ─────────────────────────
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Reduce noise from verbose third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)

    # Inject global context fields present on every log record
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service=service_name,
        service_version=service_version,
    )

    _CONFIGURED = True

    structlog.get_logger(__name__).info(
        "Logging configured",
        level=level,
        format=format,
        service=service_name,
    )
