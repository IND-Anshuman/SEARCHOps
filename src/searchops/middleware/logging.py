"""
Request/Response Logging Middleware.

Logs structured access logs for every HTTP request with:
- Method, path, status code
- Request and response size
- Duration
- Correlation ID (from RequestContextMiddleware)

NOTE: Does NOT log request/response bodies by default to avoid PII leakage.
"""
from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger(__name__)

# Paths to skip logging (too noisy, not interesting)
_EXCLUDED_PATHS = frozenset({
    "/health",
    "/health/live",
    "/health/ready",
    "/metrics",
    "/favicon.ico",
})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Structured access log for every HTTP request."""
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Log request start and completion."""
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)
        
        start_time = time.perf_counter()
        status_code = 500
        
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            log_fn = log.warning if status_code >= 400 else log.info
            log_fn(
                "HTTP request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
                client_ip=request.client.host if request.client else None,
            )
