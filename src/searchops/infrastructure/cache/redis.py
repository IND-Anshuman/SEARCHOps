"""
Redis Cache Implementation.

Implements `ICache` interface from `core.interfaces.memory` and `ICacheStore` from `core.interfaces.storage`.
Provides generic async caching with json/pickle serialization, TTL, and pattern deletion.
"""

from __future__ import annotations

from typing import Any

import orjson
import redis.asyncio as aioredis
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.core.interfaces.memory import ICache
from searchops.core.interfaces.storage import ICacheStore
from searchops.shared.json_safe import json_safe

log = structlog.get_logger(__name__)

_redis_client: aioredis.Redis | None = None


def get_redis_client(settings: Settings | None = None) -> aioredis.Redis:
    """Return or initialize the global Redis async client singleton."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    cfg = settings or get_settings()
    c_cfg = cfg.cache

    # aioredis v6 deprecated retry_on_timeout as a constructor arg
    # (TimeoutError is included by default). Only pass it on v5.
    import inspect

    _sig = inspect.signature(aioredis.Redis.__init__)
    _kwargs: dict[str, Any] = {
        "host": c_cfg.host,
        "port": c_cfg.port,
        "db": c_cfg.db,
        "password": c_cfg.password.get_secret_value() if c_cfg.password else None,
        "max_connections": c_cfg.max_connections,
        "socket_timeout": c_cfg.socket_timeout,
        "socket_connect_timeout": c_cfg.socket_connect_timeout,
        "health_check_interval": c_cfg.health_check_interval,
        "decode_responses": False,  # raw bytes for performance with orjson
        "ssl": c_cfg.ssl,
    }
    if c_cfg.ssl_cert_reqs is not None:
        _kwargs["ssl_cert_reqs"] = c_cfg.ssl_cert_reqs
    if "retry_on_timeout" in _sig.parameters:
        _kwargs["retry_on_timeout"] = c_cfg.retry_on_timeout

    _redis_client = aioredis.Redis(**_kwargs)
    log.info("Redis client connected", host=c_cfg.host, port=c_cfg.port, db=c_cfg.db)
    return _redis_client


class RedisCache(ICache, ICacheStore):
    """Concrete Redis implementation of ICache and ICacheStore interfaces."""

    def __init__(self, client: aioredis.Redis | None = None) -> None:
        self.client = client or get_redis_client()

    async def get(self, key: str) -> Any | None:
        """Get value by key."""
        try:
            val = await self.client.get(key)
            if val is None:
                return None
            return orjson.loads(val)
        except Exception as exc:
            log.warning("Cache get error", key=key, error=str(exc))
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Set key-value pair with optional TTL."""
        try:
            payload = orjson.dumps(json_safe(value))
            if ttl_seconds:
                await self.client.setex(key, ttl_seconds, payload)
            else:
                await self.client.set(key, payload)
            return True
        except Exception as exc:
            log.warning("Cache set error", key=key, error=str(exc))
            return False

    async def delete(self, key: str) -> bool:
        """Delete key."""
        try:
            res = await self.client.delete(key)
            return bool(res > 0)
        except Exception as exc:
            log.warning("Cache delete error", key=key, error=str(exc))
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            res = await self.client.exists(key)
            return bool(res > 0)
        except Exception as exc:
            log.warning("Cache exists error", key=key, error=str(exc))
            return False

    async def clear_prefix(self, prefix: str) -> int:
        """Delete all keys matching prefix pattern."""
        try:
            keys: list[bytes] = []
            async for k in self.client.scan_iter(match=f"{prefix}*"):
                keys.append(k)
            if keys:
                return await self.client.delete(*keys)
            return 0
        except Exception as exc:
            log.warning("Cache clear_prefix error", prefix=prefix, error=str(exc))
            return 0

    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter. Returns the new value."""
        try:
            return int(await self.client.incrby(key, amount))
        except Exception as exc:
            log.warning("Cache increment error", key=key, error=str(exc))
            return 0

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set a TTL on an existing key. Returns True if key was found."""
        try:
            return bool(await self.client.expire(key, ttl_seconds))
        except Exception as exc:
            log.warning("Cache expire error", key=key, error=str(exc))
            return False

    async def flush_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern. Returns count deleted."""
        try:
            keys: list[bytes] = []
            async for k in self.client.scan_iter(match=pattern):
                keys.append(k)
            if keys:
                return int(await self.client.delete(*keys))
            return 0
        except Exception as exc:
            log.warning("Cache flush_pattern error", pattern=pattern, error=str(exc))
            return 0


async def close_redis() -> None:
    """Close global Redis client connection."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        log.info("Redis connection closed")
        _redis_client = None
