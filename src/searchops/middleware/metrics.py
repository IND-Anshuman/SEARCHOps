"""
Prometheus HTTP metrics middleware.

Records:
- searchops_http_requests_total (counter)
- searchops_http_request_duration_seconds (histogram)
- searchops_http_active_requests (gauge)
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from searchops.core.observability.metrics import (
    HTTP_ACTIVE_REQUESTS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)

_EXCLUDED_PATHS = frozenset({"/metrics", "/health", "/health/live", "/health/ready"})


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Records Prometheus metrics for every HTTP request."""
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)
        
        method = request.method
        path = self._normalize_path(request.url.path)
        
        HTTP_ACTIVE_REQUESTS.labels(method=method, path=path).inc()
        start_time = time.perf_counter()
        status_code = 500
        
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            raise
        finally:
            duration = time.perf_counter() - start_time
            HTTP_ACTIVE_REQUESTS.labels(method=method, path=path).dec()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=method, path=path
            ).observe(duration)
            HTTP_REQUESTS_TOTAL.labels(
                method=method, path=path, status_code=str(status_code)
            ).inc()
    
    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize path to prevent high-cardinality labels.
        
        Replaces path segments that look like UUIDs or numeric IDs
        with a placeholder to keep Prometheus label cardinality bounded.
        """
        import re
        # Replace UUIDs
        path = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{id}",
            path,
        )
        # Replace pure numeric segments
        path = re.sub(r"/\d+", "/{id}", path)
        return path
