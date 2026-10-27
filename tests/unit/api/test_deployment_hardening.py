"""Deployment hardening: start-up checks, response headers, operator-only
endpoints, per-user job limits, the run-time ceiling and stale-job detection."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from starlette.testclient import TestClient

from searchops.application.job_state_manager import INTERRUPTED_MESSAGE, JobStateManager
from searchops.application.research_service import JobLimitExceededError, ResearchApplicationService
from searchops.config.deploy_checks import check_deployment, enforce_deployment
from searchops.config.settings import Settings, get_settings
from searchops.core.context.execution import ExecutionContext
from searchops.middleware.auth import Principal, require_service_principal, set_current_principal
from searchops.middleware.security_headers import SecurityHeadersMiddleware

pytestmark = pytest.mark.unit

GOOD_KEY = "k" * 40


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    base = {
        "APP_ENV": "production",
        "SEARCHOPS_API_KEY": GOOD_KEY,
        "CLERK_PUBLISHABLE_KEY": "",
        "API_ALLOWED_ORIGINS": '["https://app.example.com"]',
        "ALLOWED_HOSTS": "app.example.com",
        "POSTGRES_PASSWORD": "a-strong-database-password",
        "NEO4J_PASSWORD": "a-strong-graph-password",
    }
    base.update(env)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AUTH_DISABLED_EXPLICITLY", raising=False)
    get_settings.cache_clear()
    return Settings()


# ── Start-up checks ──────────────────────────────────────────────────────────


def test_a_well_configured_production_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    report = check_deployment(_settings(monkeypatch))
    assert report.errors == [] and report.warnings == []


def test_production_refuses_to_start_without_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, SEARCHOPS_API_KEY="", APP_SECRET_KEY="short")
    with pytest.raises(RuntimeError, match="No authentication is configured"):
        enforce_deployment(settings)


def test_production_refuses_wildcard_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match=r"\*"):
        enforce_deployment(_settings(monkeypatch, API_ALLOWED_ORIGINS='["*"]'))


def test_explicitly_disabled_auth_warns_but_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    monkeypatch.setenv("AUTH_DISABLED_EXPLICITLY", "true")
    get_settings.cache_clear()
    report = enforce_deployment(Settings())
    assert report.errors == []
    assert any("unauthenticated" in w for w in report.warnings)


def test_development_only_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, APP_ENV="development", SEARCHOPS_API_KEY="", APP_SECRET_KEY="short")
    assert enforce_deployment(settings).errors  # reported, but does not raise


def test_production_warns_about_dev_passwords_and_missing_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, POSTGRES_PASSWORD="searchops_dev_pass", ALLOWED_HOSTS="localhost")
    warnings = " ".join(check_deployment(settings).warnings)
    assert "POSTGRES_PASSWORD" in warnings and "ALLOWED_HOSTS" in warnings


def test_clerk_alone_is_a_complete_auth_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    pk = "pk_live_" + "Y2hhcm1pbmctaG91bmQtOTY2Mi5jbGVyay5hY2NvdW50cy5kZXYk"
    report = check_deployment(
        _settings(monkeypatch, SEARCHOPS_API_KEY="", APP_SECRET_KEY="short", CLERK_PUBLISHABLE_KEY=pk)
    )
    assert report.errors == []


# ── Headers, hosts, docs ─────────────────────────────────────────────────────


def test_security_headers_are_set_and_hsts_only_over_https() -> None:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    async def x() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    plain = client.get("/x")
    assert plain.headers["x-content-type-options"] == "nosniff"
    assert plain.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in plain.headers
    assert "max-age" in client.get("/x", headers={"X-Forwarded-Proto": "https"}).headers["strict-transport-security"]


def test_production_app_hides_docs_rejects_foreign_hosts_and_keeps_cors_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch)
    from searchops.api.main import create_app

    client = TestClient(create_app(), base_url="http://app.example.com")
    assert client.get("/docs").status_code in (404, 401)
    assert client.get("/openapi.json").status_code in (404, 401)

    foreign = TestClient(create_app(), base_url="http://evil.example.net")
    assert foreign.get("/health/live").status_code == 400

    # An auth failure must still carry CORS headers so the browser can read the 401.
    res = client.get("/api/v1/research/x", headers={"Origin": "https://app.example.com"})
    assert res.status_code == 401
    assert res.headers["access-control-allow-origin"] == "https://app.example.com"
    assert res.headers["x-frame-options"] == "DENY"


# ── Operator-only endpoints ──────────────────────────────────────────────────


def test_service_guard_refuses_clerk_users_but_not_services() -> None:
    app = FastAPI()

    @app.get("/op", dependencies=[Depends(require_service_principal)])
    async def op() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    try:
        set_current_principal(Principal("user", "user_alice"))
        assert client.get("/op").status_code == 403
        set_current_principal(Principal("service"))
        assert client.get("/op").status_code == 200
    finally:
        set_current_principal(None)


# ── Jobs: limits, ceiling, stale ─────────────────────────────────────────────


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.client = MagicMock()

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int = 0) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _manager() -> JobStateManager:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return JobStateManager(cache=_Cache(), event_bus=bus)  # type: ignore[arg-type]


async def test_user_cannot_exceed_their_concurrent_job_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_MAX_CONCURRENT_JOBS_PER_USER", "2")
    get_settings.cache_clear()
    jsm = _manager()
    service = ResearchApplicationService(cache=_Cache(), job_state_manager=jsm)  # type: ignore[arg-type]
    blocker = asyncio.Event()

    async def _hang(*_: Any, **__: Any) -> None:
        await blocker.wait()

    service._execute_research = _hang  # type: ignore[method-assign]

    first = await service.start_research("q1", owner_id="user_a")
    await service.start_research("q2", owner_id="user_a")
    with pytest.raises(JobLimitExceededError):
        await service.start_research("q3", owner_id="user_a")
    await service.start_research("other user is unaffected", owner_id="user_b")
    await service.start_research("services are unlimited")  # no owner

    await jsm.update_job(first, {"status": "completed"})  # a slot frees up
    await service.start_research("q4", owner_id="user_a")
    blocker.set()
    await asyncio.sleep(0)


async def test_job_that_exceeds_the_runtime_ceiling_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_JOB_MAX_RUNTIME_SECONDS", "1")
    get_settings.cache_clear()
    jsm = _manager()
    service = ResearchApplicationService(cache=_Cache(), job_state_manager=jsm)  # type: ignore[arg-type]
    await jsm.create_job("j1", {"status": "running"})

    async def _slow(*_: Any, **__: Any) -> None:
        await asyncio.sleep(30)

    service._run_research = _slow  # type: ignore[method-assign]
    await service._execute_research("j1", "q", "standard", 5, ExecutionContext.create())

    state = await jsm.get_job("j1")
    assert state is not None
    assert state["status"] == "failed" and "stopped" in state["error"]


async def test_job_with_a_lost_heartbeat_is_reported_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_JOB_STALE_SECONDS", "60")
    get_settings.cache_clear()
    jsm = _manager()
    await jsm.create_job("stuck", {"status": "running"})
    jsm._cache.store["research:job:stuck"]["updated_at"] = time.time() - 600  # type: ignore[attr-defined]
    await jsm.create_job("fresh", {"status": "running"})
    await jsm.create_job("done", {"status": "completed"})
    jsm._cache.store["research:job:done"]["updated_at"] = time.time() - 600  # type: ignore[attr-defined]

    stuck = await jsm.get_job("stuck")
    assert stuck is not None and stuck["status"] == "failed" and stuck["error"] == INTERRUPTED_MESSAGE
    fresh = await jsm.get_job("fresh")
    assert fresh is not None and fresh["status"] == "running"
    done = await jsm.get_job("done")
    assert done is not None and done["status"] == "completed"


def test_content_security_policy_allows_clerk_and_fonts_only() -> None:
    from searchops.config.subsystems.security import SecuritySettings
    from searchops.middleware.security_headers import build_content_security_policy

    # pk_test_ + base64("example.clerk.accounts.dev$")
    pk = "pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk"
    with_clerk = build_content_security_policy(
        SecuritySettings(CLERK_PUBLISHABLE_KEY=pk, content_security_policy="")  # type: ignore[call-arg]
    )
    directives = dict(d.split(" ", 1) for d in with_clerk.split("; "))
    assert "https://example.clerk.accounts.dev" in directives["script-src"]
    assert "https://*.protect.clerk.com:*" in directives["connect-src"]
    assert "https://challenges.cloudflare.com" in directives["frame-src"]
    assert "https://fonts.gstatic.com" in directives["font-src"]
    assert directives["frame-ancestors"] == "'none'" and directives["object-src"] == "'none'"

    without = build_content_security_policy(
        SecuritySettings(CLERK_PUBLISHABLE_KEY="", content_security_policy="")  # type: ignore[call-arg]
    )
    assert "clerk" not in without
    override = SecuritySettings(CLERK_PUBLISHABLE_KEY=pk, content_security_policy="default-src 'none'")  # type: ignore[call-arg]
    assert build_content_security_policy(override) == "default-src 'none'"
