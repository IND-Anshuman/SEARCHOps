"""
Unit tests for core FastAPI application routes (health, metrics, websocket, app factory).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from searchops.api.main import create_app
from searchops.api.v1 import health as health_module


@pytest.mark.unit
def test_create_app():
    app = create_app()
    assert app is not None
    assert app.title == "SEARCHOps Platform"


@pytest.mark.unit
def test_health_routes():
    """Health routes respond. A container with nothing configured reports every
    component as ``configured: false`` and the platform as healthy — an optional
    dependency that was never built is not a fault."""
    app = create_app()

    class _EmptyContainer:
        cache = None
        db_engine = None
        vector_store = None
        graph_client = None

    app.dependency_overrides[health_module._get_container] = lambda: _EmptyContainer()
    client = TestClient(app, follow_redirects=True)

    res_main = client.get("/health")
    assert res_main.status_code == 200
    # Every component is reported, not silently omitted.
    assert len(res_main.json()["components"]) == 4

    res_live = client.get("/health/live")
    assert res_live.status_code == 200

    res_ready = client.get("/health/ready")
    assert res_ready.status_code == 200


@pytest.mark.unit
def test_health_reports_unhealthy_dependency():
    """A configured dependency that is down must surface, not show a green light."""
    from searchops.shared.contracts.health import HealthStatusValue

    app = create_app()

    class _BrokenRedis:
        async def ping(self):
            raise ConnectionRefusedError("down")

    class _Container:
        cache = _BrokenRedis()
        db_engine = None
        vector_store = None
        graph_client = None

    app.dependency_overrides[health_module._get_container] = lambda: _Container()
    client = TestClient(app, follow_redirects=True)

    res = client.get("/health")
    assert res.status_code == 200  # the endpoint itself stays up
    body = res.json()
    assert body["status"] == HealthStatusValue.UNHEALTHY.value
    by_name = {c["name"]: c for c in body["components"]}
    assert by_name["redis"]["status"] == HealthStatusValue.UNHEALTHY.value

    # Readiness must refuse traffic rather than routing it to a broken pod.
    res_ready = client.get("/health/ready")
    assert res_ready.status_code == 503
    assert "redis" in res_ready.json()["unhealthy"]


@pytest.mark.unit
def test_metrics_route():
    app = create_app()
    client = TestClient(app, follow_redirects=True)

    # Metrics describe internals and are for the monitoring service only.
    assert client.get("/metrics").status_code == 401

    res = client.get("/metrics", headers={"X-API-Key": "test-secret-key-32-chars-minimum!"})
    assert res.status_code == 200
    assert "searchops" in res.text or "python" in res.text
