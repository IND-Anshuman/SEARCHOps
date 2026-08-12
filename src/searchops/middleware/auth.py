"""
API Authentication & Secret Key Verification Middleware.

Validates Bearer tokens or X-API-Key headers against system settings.
"""

from __future__ import annotations

from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from searchops.config.settings import get_settings

log = structlog.get_logger(__name__)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Enforces API key or Bearer token validation on protected endpoints."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.settings = get_settings().security

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        settings = get_settings()

        # Exclude CORS OPTIONS preflight requests, non-API/non-WS endpoints, and development/testing environments
        if (
            request.method == "OPTIONS"
            or (not path.startswith("/api/v1") and not path.startswith("/ws"))
            or settings.env in ("development", "testing")
        ):
            return await call_next(request)

        sec_settings = settings.security
        sec_key = getattr(sec_settings, "api_key", None) or getattr(sec_settings, "secret_key", None)
        expected_key = sec_key.get_secret_value() if hasattr(sec_key, "get_secret_value") else str(sec_key or "")
        if not expected_key:
            # If no API key configured, pass through
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            provided_key = auth_header[7:].strip()

        if not provided_key or provided_key != expected_key:
            log.warning("Unauthorized API access attempt", path=path, client_ip=request.client.host if request.client else "unknown")
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing API key."},
            )

        return await call_next(request)
