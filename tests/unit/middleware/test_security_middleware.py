"""
Unit tests for RateLimiterMiddleware and APIKeyAuthMiddleware.
"""

from __future__ import annotations

import pytest
from searchops.middleware.rate_limiter import RedisSlidingWindowRateLimiter


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sliding_window_rate_limiter_memory_fallback():
    limiter = RedisSlidingWindowRateLimiter(redis_client=None, limit=2, window_seconds=60)
    
    allowed1, remaining1, _ = await limiter.is_allowed("client_1")
    assert allowed1 is True
    assert remaining1 == 1

    allowed2, remaining2, _ = await limiter.is_allowed("client_1")
    assert allowed2 is True
    assert remaining2 == 0

    allowed3, remaining3, _ = await limiter.is_allowed("client_1")
    assert allowed3 is False
    assert remaining3 == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sliding_window_rate_limiter_distinct_keys():
    limiter = RedisSlidingWindowRateLimiter(redis_client=None, limit=1, window_seconds=60)

    allowed_c1, _, _ = await limiter.is_allowed("client_1")
    allowed_c2, _, _ = await limiter.is_allowed("client_2")

    assert allowed_c1 is True
    assert allowed_c2 is True
