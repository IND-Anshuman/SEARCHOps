"""
Shared HTTP Transport Pool managing persistent httpx.AsyncClient connections.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from searchops.config.settings import Settings, get_settings

log = structlog.get_logger(__name__)

_transport_pool: httpx.AsyncClient | None = None
_transport_loop: asyncio.AbstractEventLoop | None = None


def get_transport_pool(settings: Settings | None = None) -> httpx.AsyncClient:
    """Return or initialize global httpx.AsyncClient connection pool.

    An ``AsyncClient`` is bound to the event loop that first used it. Because
    this pool is a process-global singleton, a caller running on a *different*
    loop (a fresh event loop, a test session, a worker) would otherwise reuse
    the stale client and fail with ``Event loop is closed``. The loop is
    tracked and the pool rebuilt when it no longer matches.
    """
    global _transport_pool, _transport_loop

    try:
        current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _transport_pool is not None and not _transport_pool.is_closed and _transport_loop is current_loop:
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
    _transport_loop = current_loop
    log.info("HTTP Transport Pool initialized", max_connections=100)
    return _transport_pool


async def close_transport_pool() -> None:
    """Close global httpx connection pool."""
    global _transport_pool, _transport_loop
    if _transport_pool is not None and not _transport_pool.is_closed:
        try:
            await _transport_pool.aclose()
        except RuntimeError:
            pass
        log.info("HTTP Transport Pool closed")
    _transport_pool = None
    _transport_loop = None
