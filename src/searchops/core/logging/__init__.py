"""Core logging package."""

from searchops.core.logging.configure import configure_logging
from searchops.core.logging.processors import (
    add_correlation_id,
    add_open_telemetry_ids,
    redact_sensitive_fields,
)

__all__ = [
    "configure_logging",
    "add_correlation_id",
    "add_open_telemetry_ids",
    "redact_sensitive_fields",
]
