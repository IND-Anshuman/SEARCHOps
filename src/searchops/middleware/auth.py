"""
API Authentication & Secret Key Verification Middleware.

Validates Bearer tokens or X-API-Key headers against system settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from searchops.config.settings import get_settings

if TYPE_CHECKING:
    from starlette.requests import Request

log = structlog.get_logger(__name__)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Enforces API key or Bearer token validation on protected endpoints."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.settings = get_settings().security

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        settings = get_settings()

        # Exclude CORS preflight and anything outside the protected surfaces.
        #
        # Deliberately NOT excluded: the environment. The previous version
        # skipped verification whenever env was development or testing, and the
        # shipped .env sets APP_ENV=development — so the default deployment had
        # no authentication at all. Auth is a property of the deployment, not
        # of the environment name; disabling it requires the explicit opt-in
        # below so it is always a conscious, logged decision.
        if request.method == "OPTIONS":
            return await call_next(request)
        if not path.startswith("/api/v1") and not path.startswith("/ws"):
            return await call_next(request)

        sec_settings = settings.security
        if sec_settings.auth_disabled_explicitly:
            log.warning(
                "auth.middleware.disabled_explicitly",
                path=path,
                hint="SEARCHOPS_AUTH_DISABLED_EXPLICITLY is set; API is unauthenticated",
            )
            return await call_next(request)

        # api_key is the dedicated request credential and wins when set, so it
        # can be rotated without invalidating every JWT signed by secret_key.
        sec_key = sec_settings.api_key or sec_settings.secret_key
        expected_key: str = (
            sec_key.get_secret_value()
            if sec_key is not None and hasattr(sec_key, "get_secret_value")
            else str(sec_key or "")
        )
        if not expected_key:
            # No credential is configured at all — there is nothing to check
            # against. Fail closed rather than open: an unconfigured key is a
            # deployment mistake, not an invitation.
            log.error(
                "auth.middleware.no_key_configured",
                path=path,
                hint="set APP_SECRET_KEY or SEARCHOPS_API_KEY",
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Server authentication is not configured. Set APP_SECRET_KEY or SEARCHOPS_API_KEY."},
            )

        provided_key = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            provided_key = auth_header[7:].strip()

        if not provided_key or provided_key != expected_key:
            log.warning(
                "Unauthorized API access attempt",
                path=path,
                client_ip=request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing API key."},
            )

        return await call_next(request)
