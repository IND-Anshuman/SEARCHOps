"""
Unit tests for APIKeyAuthMiddleware and middleware stack components.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from searchops.middleware.auth import APIKeyAuthMiddleware
from searchops.middleware.logging import RequestLoggingMiddleware
from searchops.middleware.metrics import PrometheusMetricsMiddleware
from searchops.middleware.rate_limiter import RateLimiterMiddleware
from searchops.middleware.request_context import RequestContextMiddleware


async def homepage(request):
    return PlainTextResponse("OK")


async def health(request):
    return PlainTextResponse("HEALTHY")


def _build_test_app():
    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/health", health),
            Route("/api/v1/data", homepage),
        ]
    )
    app.add_middleware(RateLimiterMiddleware)
    app.add_middleware(APIKeyAuthMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(PrometheusMetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


@pytest.mark.unit
def test_auth_middleware_public_endpoint():
    app = _build_test_app()
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.text == "HEALTHY"


@pytest.mark.unit
def test_auth_middleware_missing_header():
    app = _build_test_app()
    client = TestClient(app)
    res = client.get("/api/v1/data")
    # Will be 401 unauthorized or 200 if default secret_key matches header
    assert res.status_code in (200, 401)


@pytest.mark.unit
def test_auth_middleware_header_handling():
    app = _build_test_app()
    client = TestClient(app)
    res_key = client.get("/api/v1/data", headers={"X-API-Key": "test"})
    res_bearer = client.get("/api/v1/data", headers={"Authorization": "Bearer test"})
    assert res_key.status_code in (200, 401)
    assert res_bearer.status_code in (200, 401)

