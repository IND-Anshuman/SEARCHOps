"""Core logging package."""

from searchops.core.logging.configure import configure_logging
from searchops.core.logging.processors import (
    add_correlation_id,
    add_open_telemetry_ids,
    redact_sensitive_fields,
)

__all__ = [
    "add_correlation_id",
    "add_open_telemetry_ids",
    "configure_logging",
    "redact_sensitive_fields",
]
