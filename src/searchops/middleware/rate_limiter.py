"""
Redis Sliding-Window Rate Limiter Middleware.

Uses Redis sorted sets (ZADD, ZREMRANGEBYSCORE, ZCARD) to enforce
sliding window rate limits per client IP or API Key.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = structlog.get_logger(__name__)


class RedisSlidingWindowRateLimiter:
    """Atomic Redis sliding window rate limiter implementation."""

    def __init__(self, redis_client: Any | None = None, limit: int = 100, window_seconds: int = 60) -> None:
        self.redis_client = redis_client
        self.limit = limit
        self.window_seconds = window_seconds
        self._memory_store: dict[str, list[float]] = {}  # In-memory fallback

    async def is_allowed(self, key: str) -> tuple[bool, int, int]:
        """Check if request is allowed under rate limit window. Returns (allowed, remaining, limit)."""
        now = time.time()
        clear_before = now - self.window_seconds

        if self.redis_client:
            try:
                redis_key = f"ratelimit:{key}"
                pipe = self.redis_client.pipeline()
                pipe.zremrangebyscore(redis_key, 0, clear_before)
                pipe.zadd(redis_key, {str(now): now})
                pipe.zcard(redis_key)
                pipe.expire(redis_key, self.window_seconds)
                results = await pipe.execute()
                current_count = results[2]
                remaining = max(0, self.limit - current_count)
                allowed = current_count <= self.limit
                return allowed, remaining, self.limit
            except Exception as exc:
                log.warning("Redis rate limiter failed, falling back to memory", error=str(exc))

        # Memory fallback
        if key not in self._memory_store:
            self._memory_store[key] = []

        timestamps = [t for t in self._memory_store[key] if t > clear_before]
        timestamps.append(now)
        self._memory_store[key] = timestamps

        current_count = len(timestamps)
        remaining = max(0, self.limit - current_count)
        allowed = current_count <= self.limit
        return allowed, remaining, self.limit


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Starlette middleware enforcing rate limiting."""

    def __init__(self, app: Any, rate_limiter: RedisSlidingWindowRateLimiter | None = None) -> None:
        super().__init__(app)
        self.rate_limiter = rate_limiter or RedisSlidingWindowRateLimiter()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path.startswith("/health") or path.startswith("/docs") or path.startswith("/redoc") or path == "/openapi.json":
            return await call_next(request)

        client_key = request.headers.get("X-API-Key") or (request.client.host if request.client else "unknown")
        allowed, remaining, limit = await self.rate_limiter.is_allowed(client_key)

        if not allowed:
            log.warning("Rate limit exceeded", client_key=client_key, path=path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please try again later."},
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": "60",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
