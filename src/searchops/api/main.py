"""
FastAPI application factory.

This module creates and configures the FastAPI application instance.
It is the topmost application layer — all requests flow through here.

The application ONLY knows about:
- Bootstrap (lifespan, container)
- Middleware
- Routers (added in later phases)
- Exception handlers

It does NOT know about:
- Business logic
- Infrastructure
- Database
- LLM providers
"""

from __future__ import annotations

from pathlib import Path

import structlog
from dotenv import load_dotenv

# Auto-load .env file before anything initializes
env_file = Path(__file__).resolve().parents[3] / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file)
else:
    load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from searchops.bootstrap.lifespan import create_lifespan
from searchops.config.settings import get_settings
from searchops.core.exceptions.handlers import register_exception_handlers
from searchops.middleware.logging import RequestLoggingMiddleware
from searchops.middleware.metrics import PrometheusMetricsMiddleware
from searchops.middleware.request_context import RequestContextMiddleware

log = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    settings = get_settings()

    # Fail fast on a configuration that would leave a public deployment open.
    from searchops.config.deploy_checks import enforce_deployment

    enforce_deployment(settings)

    # Interactive docs describe every endpoint; production hides them unless asked.
    docs_hidden = settings.env == "production" and not settings.api.expose_docs

    app = FastAPI(
        title="SEARCHOps Platform",
        description="Enterprise Autonomous Technology Intelligence Platform",
        version=settings.app_version,
        docs_url=None if docs_hidden else settings.api.docs_url,
        redoc_url=None if docs_hidden else settings.api.redoc_url,
        openapi_url=None if docs_hidden else settings.api.openapi_url,
        root_path=settings.api.root_path,
        lifespan=create_lifespan,
    )

    # ─── Middleware (order matters — outermost first) ──────────────────────
    # Starlette wraps each added middleware around the previous ones, so the
    # LAST one added is outermost. CORS is added last (below) so that even the
    # 401/403/429 responses produced by auth and rate limiting carry CORS
    # headers; otherwise a cross-origin browser sees an opaque network error
    # instead of the status it needs to refresh its session token.

    # Prometheus metrics (wraps all requests)
    if settings.observability.prometheus_enabled:
        app.add_middleware(PrometheusMetricsMiddleware)

    # Request logging (after metrics so we log the final status code)
    app.add_middleware(RequestLoggingMiddleware)

    # Request context (must be innermost middleware so context is available to all above)
    app.add_middleware(RequestContextMiddleware)

    # ─── Security & Rate Limit Middleware ────────────────────────────────────
    from searchops.middleware.auth import APIKeyAuthMiddleware
    from searchops.middleware.rate_limiter import RateLimiterMiddleware

    app.add_middleware(RateLimiterMiddleware)
    app.add_middleware(APIKeyAuthMiddleware)

    # Host-header allowlist (public deployments only): stops requests that
    # reach the app under a name you did not configure. localhost stays allowed
    # so container health probes keep working.
    if settings.env in ("production", "staging"):
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        hosts = list(dict.fromkeys([*settings.security.allowed_hosts, "localhost", "127.0.0.1"]))
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    from searchops.middleware.security_headers import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.allowed_origins,
        allow_credentials=settings.api.cors_allow_credentials,
        allow_methods=settings.api.cors_allow_methods,
        allow_headers=settings.api.cors_allow_headers,
    )

    # ─── Exception handlers ────────────────────────────────────────
    register_exception_handlers(app)

    # ─── Core routers (always mounted) ──────────────────────────────
    from searchops.api.v1.health import router as health_router
    from searchops.api.v1.metrics import router as metrics_router

    app.include_router(health_router, prefix="/health", tags=["Health"])
    app.include_router(metrics_router, prefix="", tags=["Metrics"])

    # ─── Browser sign-in (API key → HttpOnly session cookie) ───────────
    from searchops.api.v1.auth import router as auth_router

    app.include_router(auth_router, prefix="/api/v1")

    # ─── Research routers ────────────────────────────────────────────
    from searchops.api.v1.research import router as research_router
    from searchops.api.v1.websocket import router as ws_router

    app.include_router(research_router, prefix="/api/v1")
    app.include_router(ws_router)

    # ─── Derived artifacts (glossary, next steps, trust, study guide) ────────
    from searchops.api.v1.derived import router as derived_router

    app.include_router(derived_router, prefix="/api/v1")

    # ─── Agent protocols (MCP tool execution, A2A messaging) ───────────────
    from searchops.api.v1.agent_protocols import router as agent_protocols_router

    app.include_router(agent_protocols_router, prefix="/api/v1")

    # ── Frontend static files ──────────────────────────────────────────
    from fastapi.staticfiles import StaticFiles

    frontend_dist = None
    current_path = Path(__file__).resolve()
    for parent in [current_path, *list(current_path.parents)]:
        candidate = parent / "frontend" / "dist"
        if candidate.exists() and candidate.is_dir():
            frontend_dist = candidate
            break

    if not frontend_dist:
        candidate = Path.cwd() / "frontend" / "dist"
        if candidate.exists() and candidate.is_dir():
            frontend_dist = candidate

    if frontend_dist:
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
        log.info("Frontend static assets mounted at root /", path=str(frontend_dist))
    else:
        log.warning("Frontend static assets directory not found")

    log.info(
        "FastAPI application configured",
        version=settings.app_version,
        env=settings.env,
    )

    return app


# Module-level app instance (used by uvicorn)
app = create_app()
