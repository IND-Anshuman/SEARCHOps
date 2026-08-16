"""
Shared HTTP Transport Pool managing persistent httpx.AsyncClient connections.
"""

from __future__ import annotations

import httpx
import structlog

from searchops.config.settings import Settings, get_settings

log = structlog.get_logger(__name__)

_transport_pool: httpx.AsyncClient | None = None


def get_transport_pool(settings: Settings | None = None) -> httpx.AsyncClient:
    """Return or initialize global httpx.AsyncClient connection pool."""
    global _transport_pool
    if _transport_pool is not None and not _transport_pool.is_closed:
        return _transport_pool

    cfg = settings or get_settings()

    limits = httpx.Limits(
        max_keepalive_connections=20,
        max_connections=100,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(
        connect=10.0,
        read=cfg.scraping.request_timeout if hasattr(cfg, "scraping") else 15.0,
        write=10.0,
        pool=5.0,
    )

    _transport_pool = httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "SEARCHOps-AutonomousAgent/1.0"},
    )
    log.info("HTTP Transport Pool initialized", max_connections=100)
    return _transport_pool


async def close_transport_pool() -> None:
    """Close global httpx connection pool."""
    global _transport_pool
    if _transport_pool is not None and not _transport_pool.is_closed:
        await _transport_pool.aclose()
        log.info("HTTP Transport Pool closed")
        _transport_pool = None
