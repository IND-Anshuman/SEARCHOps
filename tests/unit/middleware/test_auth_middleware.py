"""
Unit tests for APIKeyAuthMiddleware and middleware stack components.

The three original tests below asserted ``status_code in (200, 401)``.
That passes whether or not auth is enforced, so a middleware that had
stopped enforcing anything at all still looked green.

The strict tests added here pin down the behaviour that actually matters:

1. Auth is **never** disabled merely because ``APP_ENV`` is development or
   testing. The middleware used to skip verification entirely in those
   environments, and the shipped ``.env`` sets ``APP_ENV=development``, so
   the default deployment had no authentication at all.
2. A dedicated ``SEARCHOPS_API_KEY`` is honoured for ``X-API-Key`` auth. The
   middleware read ``security.api_key``, an attribute that does not exist on
   ``SecuritySettings``, so that header could never authenticate.
3. Disabling auth requires an explicit, named opt-in -- and it is logged.

The three original loose tests are kept at the bottom under their own names
so the diff stays reviewable; they are now redundant with the strict ones.
"""

from __future__ import annotations

import functools

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from searchops.config.settings import get_settings
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


# ── Strict auth tests ────────────────────────────────────────────────────────

KEY = "k" * 32


def _build_auth_only_app():
    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/api/v1/data", homepage),
            Route("/ws/research/1", homepage),
        ]
    )
    app.add_middleware(APIKeyAuthMiddleware)
    return app


def _reset_settings(monkeypatch, *, env, secret=KEY, api_key=None, disabled=False):
    """Point the middleware at a Settings built from a controlled environment.

    ``get_settings`` is lru_cached, so the cache must be cleared for the
    monkeypatched env vars to take effect, and the middleware module's
    imported reference must be repointed at the same callable.
    """
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("APP_SECRET_KEY", secret)
    if api_key is None:
        # Empty, not deleted: a deleted variable falls back to the developer's .env.
        monkeypatch.setenv("SEARCHOPS_API_KEY", "")
    else:
        monkeypatch.setenv("SEARCHOPS_API_KEY", api_key)
    if disabled:
        monkeypatch.setenv("AUTH_DISABLED_EXPLICITLY", "true")
    else:
        monkeypatch.delenv("AUTH_DISABLED_EXPLICITLY", raising=False)

    get_settings.cache_clear()
    fresh = functools.lru_cache(maxsize=1)(lambda: get_settings())
    monkeypatch.setattr("searchops.middleware.auth.get_settings", fresh)
    return fresh()


@pytest.mark.unit
def test_protected_route_rejects_missing_key_in_development(monkeypatch):
    """Regression: the shipped .env sets APP_ENV=development, which used to
    disable authentication entirely for the default deployment."""
    _reset_settings(monkeypatch, env="development")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data")
    assert res.status_code == 401


@pytest.mark.unit
def test_protected_route_rejects_missing_key_in_testing(monkeypatch):
    _reset_settings(monkeypatch, env="testing")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data")
    assert res.status_code == 401


@pytest.mark.unit
def test_protected_route_rejects_missing_key_in_production(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data")
    assert res.status_code == 401


@pytest.mark.unit
def test_websocket_route_is_protected(monkeypatch):
    _reset_settings(monkeypatch, env="development")
    res = TestClient(_build_auth_only_app()).get("/ws/research/1")
    assert res.status_code == 401


@pytest.mark.unit
def test_public_health_route_is_not_protected(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).get("/health")
    assert res.status_code == 200
    assert res.text == "HEALTHY"


@pytest.mark.unit
def test_options_preflight_is_not_blocked(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).options("/api/v1/data")
    assert res.status_code != 401


@pytest.mark.unit
def test_secret_key_authenticates_via_x_api_key(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data", headers={"X-API-Key": KEY})
    assert res.status_code == 200


@pytest.mark.unit
def test_secret_key_authenticates_via_bearer(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data", headers={"Authorization": f"Bearer {KEY}"})
    assert res.status_code == 200


@pytest.mark.unit
def test_wrong_key_is_rejected(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data", headers={"X-API-Key": "wrong"})
    assert res.status_code == 401


@pytest.mark.unit
def test_empty_key_is_rejected(monkeypatch):
    _reset_settings(monkeypatch, env="production")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data", headers={"X-API-Key": ""})
    assert res.status_code == 401


@pytest.mark.unit
def test_dedicated_api_key_field_exists_and_authenticates(monkeypatch):
    """Regression: the middleware read security.api_key, which did not exist
    on SecuritySettings, so X-API-Key auth could never succeed."""
    settings = _reset_settings(monkeypatch, env="production", api_key="dedicated-key-value")
    assert settings.security.api_key is not None
    res = TestClient(_build_auth_only_app()).get("/api/v1/data", headers={"X-API-Key": "dedicated-key-value"})
    assert res.status_code == 200


@pytest.mark.unit
def test_dedicated_api_key_is_preferred_over_secret_key(monkeypatch):
    _reset_settings(monkeypatch, env="production", api_key="dedicated-key-value")
    res = TestClient(_build_auth_only_app()).get("/api/v1/data", headers={"X-API-Key": KEY})
    assert res.status_code == 401


@pytest.mark.unit
def test_auth_can_only_be_disabled_by_explicit_opt_in(monkeypatch):
    _reset_settings(monkeypatch, env="production", disabled=True)
    res = TestClient(_build_auth_only_app()).get("/api/v1/data")
    assert res.status_code == 200


@pytest.mark.unit
def test_explicit_disable_defaults_to_false(monkeypatch):
    settings = _reset_settings(monkeypatch, env="production")
    assert settings.security.auth_disabled_explicitly is False
