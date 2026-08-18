"""
Unit tests for core FastAPI application routes (health, metrics, websocket, app factory).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from searchops.api.main import create_app
from searchops.api.v1.health import router as health_router
from searchops.api.v1.metrics import router as metrics_router


@pytest.mark.unit
def test_create_app():
    app = create_app()
    assert app is not None
    assert app.title == "SEARCHOps Platform"


@pytest.mark.unit
def test_health_routes():
    app = create_app()
    client = TestClient(app, follow_redirects=True)

    res_main = client.get("/health")
    assert res_main.status_code == 200

    res_live = client.get("/health/live")
    assert res_live.status_code == 200

    res_ready = client.get("/health/ready")
    assert res_ready.status_code == 200


@pytest.mark.unit
def test_metrics_route():
    app = create_app()
    client = TestClient(app, follow_redirects=True)

    res = client.get("/metrics")
    assert res.status_code == 200
    assert "searchops" in res.text or "python" in res.text
