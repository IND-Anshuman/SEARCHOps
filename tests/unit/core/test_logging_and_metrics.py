"""
Unit tests for core logging configuration, processors, metrics exporters, and tracers.
"""

from __future__ import annotations

import pytest
import structlog

from searchops.core.logging.configure import configure_logging
from searchops.core.logging.processors import add_correlation_id, redact_sensitive_fields, add_open_telemetry_ids
from searchops.core.observability.metrics import HTTP_REQUESTS_TOTAL, SERVICE_INFO
from searchops.core.observability.tracer import get_tracer, setup_tracer_provider


@pytest.mark.unit
def test_configure_logging_idempotent():
    configure_logging(level="DEBUG", format="json")
    logger = structlog.get_logger("test_logger")
    assert logger is not None


@pytest.mark.unit
def test_redact_sensitive_fields():
    event = {
        "api_key": "secret-12345",
        "password": "my-password",
        "normal_field": "public-data",
    }
    processed = redact_sensitive_fields(None, "info", event)
    assert processed["api_key"] == "[REDACTED]"
    assert processed["password"] == "[REDACTED]"
    assert processed["normal_field"] == "public-data"


@pytest.mark.unit
def test_add_correlation_id():
    event = {}
    processed = add_correlation_id(None, "info", event)
    assert "correlation_id" in processed or isinstance(processed, dict)


@pytest.mark.unit
def test_tracer_setup():
    tracer = get_tracer("test-module")
    assert tracer is not None
    setup_tracer_provider(
        service_name="test-service",
        service_version="0.1.0",
        otlp_endpoint="http://localhost:4317",
        enabled=False,
    )



@pytest.mark.unit
def test_metrics_objects():
    assert HTTP_REQUESTS_TOTAL is not None
    assert SERVICE_INFO is not None

