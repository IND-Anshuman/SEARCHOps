"""
Request Context Middleware.

For every incoming HTTP request:
1. Extracts or generates X-Request-ID and X-Correlation-ID headers
2. Creates a RequestContext
3. Binds it to contextvars so it's available throughout the request
4. Adds the IDs to the response headers
5. Resets contextvars after the response
"""
from __future__ import annotations

import time
from typing import Callable

import shortuuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from searchops.core.context.request import RequestContext
from searchops.core.context.vars import (
    current_correlation_id,
    current_request_context,
    current_trace_id,
)
from searchops.core.constants.system import (
    HEADER_CORRELATION_ID,
    HEADER_REQUEST_ID,
    HEADER_TRACE_ID,
)

log = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Injects request context into every HTTP request.
    
    Must be registered BEFORE any logging or tracing middleware
    so those middlewares can read the context.
    """
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Create and bind request context, then forward the request."""
        request_id = (
            request.headers.get(HEADER_REQUEST_ID) or shortuuid.uuid()
        )
        correlation_id = (
            request.headers.get(HEADER_CORRELATION_ID) or shortuuid.uuid()
        )
        
        ctx = RequestContext(
            request_id=request_id,
            correlation_id=correlation_id,
            path=request.url.path,
            method=request.method,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
        )
        
        # Bind to contextvars
        token_request = current_request_context.set(ctx)
        token_correlation = current_correlation_id.set(correlation_id)
        
        # Also bind structlog context for this request
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
        )
        
        try:
            response = await call_next(request)
        finally:
            # Always reset contextvars and structlog context
            current_request_context.reset(token_request)
            current_correlation_id.reset(token_correlation)
            structlog.contextvars.clear_contextvars()
        
        # Propagate IDs in response headers
        response.headers[HEADER_REQUEST_ID] = request_id
        response.headers[HEADER_CORRELATION_ID] = correlation_id
        
        return response
