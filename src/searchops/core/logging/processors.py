"""
Custom structlog processors.

Each processor is a pure function with the signature:
    (logger, method_name, event_dict) -> event_dict

Processors are composable and independently testable.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger


# ── Sensitive field redaction ─────────────────────────────────────────────────

_REDACTED = "[REDACTED]"

_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
    re.compile(r"ssn", re.IGNORECASE),
    re.compile(r"credit[_-]?card", re.IGNORECASE),
]


def _is_sensitive_key(key: str) -> bool:
    """Return True if the key name suggests it holds sensitive data."""
    return any(pattern.search(key) for pattern in _SENSITIVE_PATTERNS)


def redact_sensitive_fields(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Redact sensitive values from the event dict before emission.

    This processor protects against accidental credential leakage in logs.
    It scans all string keys and replaces values whose key names match
    known sensitive patterns with '[REDACTED]'.
    """
    for key in list(event_dict.keys()):
        if _is_sensitive_key(key):
            event_dict[key] = _REDACTED
    return event_dict


# ── OpenTelemetry correlation ─────────────────────────────────────────────────

def add_open_telemetry_ids(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Inject OTel trace_id and span_id into every log record.

    This enables correlation between logs and traces in observability backends
    like Grafana Tempo, Jaeger, and Datadog.

    If no active span exists (e.g., during startup), the fields are empty strings.
    """
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        ctx = span.get_span_context()

        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
            event_dict["trace_flags"] = format(ctx.trace_flags, "02x")
        else:
            event_dict.setdefault("trace_id", "")
            event_dict.setdefault("span_id", "")
    except ImportError:
        event_dict.setdefault("trace_id", "")
        event_dict.setdefault("span_id", "")

    return event_dict


# ── Request correlation ───────────────────────────────────────────────────────

def add_correlation_id(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Inject correlation_id and request_id from ContextVars.

    These are set by RequestContextMiddleware at the start of each
    HTTP request, ensuring every log line within a request shares the
    same correlation identifiers.
    """
    try:
        from searchops.core.context.vars import (
            current_correlation_id,
            current_trace_id,
        )

        correlation_id = current_correlation_id.get("")
        if correlation_id:
            event_dict.setdefault("correlation_id", correlation_id)

        trace_id = current_trace_id.get("")
        if trace_id and not event_dict.get("trace_id"):
            event_dict["trace_id"] = trace_id

    except ImportError:
        pass

    return event_dict


# ── Exception formatting ──────────────────────────────────────────────────────

def format_exception_chain(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Format exception chains into a structured dict.

    Instead of rendering a raw traceback string, this processor emits
    a structured 'exception' dict that log aggregators can index properly.
    """
    exc_info = event_dict.pop("exc_info", None)
    if exc_info is True:
        import sys
        exc_info = sys.exc_info()

    if exc_info and exc_info[0] is not None:
        exc_type, exc_value, exc_tb = exc_info
        event_dict["exception"] = {
            "type": exc_type.__name__ if exc_type else "Unknown",
            "message": str(exc_value),
            "module": exc_type.__module__ if exc_type else "",
        }
        # Keep the full traceback in a separate field for log aggregators
        import traceback
        event_dict["traceback"] = "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)
        )

    return event_dict
