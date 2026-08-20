"""
Bright Data CDP Connection Pool.

Manages a bounded pool of persistent Playwright→BrightData CDP browser
sessions, replacing the per-request `async with async_playwright()` pattern
that creates a new WebSocket connection for every scrape call.

Problem with per-request CDP connections:
  - BD zones have connection limits (~10-50 concurrent connections)
  - 100 concurrent requests create 100 WebSocket connections → zone limit exceeded
  - Each connection has 300-800ms cold start overhead
  - Connections are never reused → massive resource waste

This pool:
  - Maintains `max_connections` persistent CDP browser sessions
  - Uses asyncio.Semaphore to bound concurrent access
  - Recycles sessions after `recycle_after` requests (memory leak prevention)
  - Runs health checks and auto-replaces dead sessions
  - Tracks active connection count in Prometheus gauge
  - Gracefully shuts down all sessions on application shutdown

Lifecycle:
  1. Call `await pool.initialize(customer_id, zone, password)` at app startup
  2. Use `async with pool.acquire() as session:` in scraper methods
  3. Call `await pool.shutdown()` at app shutdown

The pool is injected into BrightDataBrowserScraper as a dependency.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncGenerator

import structlog

from searchops.scraping.bd_auth import build_browser_cdp_url
from searchops.scraping.bd_metrics import (
    BD_BROWSER_ACTIVE_CONNECTIONS,
    BD_BROWSER_POOL_SIZE,
    BD_BROWSER_POOL_WAIT,
)

log = structlog.get_logger(__name__)


@dataclass
class BDSession:
    """A single active Bright Data CDP browser session."""

    browser: object  # playwright.async_api.Browser
    created_at: float = field(default_factory=time.monotonic)
    request_count: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    def is_healthy(self) -> bool:
        """Check if the underlying Playwright browser is still connected."""
        try:
            return bool(getattr(self.browser, "is_connected", lambda: True)())
        except Exception:
            return False


class BDCDPPool:
    """
    Persistent pool of Bright Data Cloud Browser CDP sessions.

    Thread-safe for use in async contexts. All state mutations are
    protected by asyncio.Lock. The semaphore limits concurrent usage
    without locking the entire pool.

    Example:
        pool = BDCDPPool(max_connections=5)
        await pool.initialize(customer_id="brd-customer-...", zone="scraping_browser", password="...")

        async with pool.acquire() as session:
            page = await session.browser.new_page()
            await page.goto("https://example.com")
            html = await page.content()
            await page.close()
    """

    def __init__(
        self,
        max_connections: int = 5,
        recycle_after: int = 50,
        acquire_timeout_sec: float = 30.0,
        session_idle_timeout_sec: float = 300.0,
    ) -> None:
        """
        Args:
            max_connections: Maximum concurrent CDP sessions.
                             Match to your BD zone's connection limit.
            recycle_after: Recycle a session after this many requests.
                           Prevents memory leaks in long-running processes.
            acquire_timeout_sec: Max wait time to acquire a pool slot.
            session_idle_timeout_sec: Close idle sessions after this duration.
        """
        self._max_connections = max_connections
        self._recycle_after = recycle_after
        self._acquire_timeout = acquire_timeout_sec
        self._idle_timeout = session_idle_timeout_sec

        self._sessions: list[BDSession] = []
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_connections)
        self._pool_lock: asyncio.Lock = asyncio.Lock()
        self._initialized: bool = False
        self._shutdown_event: asyncio.Event = asyncio.Event()

        # Credentials — stored transiently, not as loggable attributes
        self._customer_id: str = ""
        self._zone: str = ""
        self._password: str = ""

        # Background maintenance task
        self._maintenance_task: asyncio.Task | None = None

        BD_BROWSER_POOL_SIZE.set(0)

    async def initialize(
        self,
        customer_id: str,
        zone: str,
        password: str,
    ) -> None:
        """
        Start the pool by establishing initial CDP connections.

        Args:
            customer_id: BD customer ID.
            zone: BD zone name for scraping browser.
            password: Zone password.
        """
        if self._initialized:
            return

        self._customer_id = customer_id
        self._zone = zone
        self._password = password

        log.info("bd_cdp_pool.initialize", max_connections=self._max_connections)

        # Pre-warm with one connection to validate credentials
        try:
            session = await self._create_session()
            async with self._pool_lock:
                self._sessions.append(session)
            BD_BROWSER_POOL_SIZE.set(len(self._sessions))
            log.info("bd_cdp_pool.initialized", sessions=len(self._sessions))
        except Exception as exc:
            log.error("bd_cdp_pool.initialize_failed", error=str(exc))
            raise

        self._initialized = True
        # Start background maintenance loop
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def shutdown(self) -> None:
        """
        Gracefully close all CDP connections and stop the pool.

        Should be called at application shutdown (lifespan hook).
        """
        log.info("bd_cdp_pool.shutdown", sessions=len(self._sessions))
        self._shutdown_event.set()

        if self._maintenance_task and not self._maintenance_task.done():
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass

        async with self._pool_lock:
            for session in self._sessions:
                try:
                    await asyncio.wait_for(
                        getattr(session.browser, "close", lambda: None)(),
                        timeout=5.0,
                    )
                except Exception:
                    pass
            self._sessions.clear()

        BD_BROWSER_POOL_SIZE.set(0)
        BD_BROWSER_ACTIVE_CONNECTIONS.set(0)
        self._initialized = False
        log.info("bd_cdp_pool.shutdown_complete")

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[BDSession, None]:
        """
        Acquire a pooled CDP browser session.

        Waits up to `acquire_timeout_sec` for a slot in the semaphore.
        Automatically returns the session to the pool on context exit.
        Recycles sessions that have exceeded `recycle_after` requests.

        Raises:
            asyncio.TimeoutError: If no pool slot becomes available in time.
        """
        if not self._initialized:
            raise RuntimeError("BDCDPPool.initialize() must be called before acquiring sessions")

        wait_start = time.monotonic()
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._acquire_timeout,
            )
        except asyncio.TimeoutError:
            log.error("bd_cdp_pool.acquire_timeout", timeout=self._acquire_timeout)
            raise

        wait_seconds = time.monotonic() - wait_start
        BD_BROWSER_POOL_WAIT.observe(wait_seconds)
        BD_BROWSER_ACTIVE_CONNECTIONS.inc()

        session = await self._get_or_create_session()
        try:
            yield session
        finally:
            session.request_count += 1
            BD_BROWSER_ACTIVE_CONNECTIONS.dec()
            self._semaphore.release()

            # Schedule recycling if needed (don't block the caller)
            if session.request_count >= self._recycle_after:
                asyncio.create_task(self._recycle_session(session))

    async def health_check(self) -> bool:
        """
        Check if the pool has at least one healthy session or can create one.

        Returns:
            True if the pool is operational.
        """
        if not self._initialized:
            return False
        try:
            # Check if any existing session is healthy
            async with self._pool_lock:
                healthy = any(s.is_healthy() for s in self._sessions)
            if healthy:
                return True
            # Try creating a probe session
            session = await asyncio.wait_for(self._create_session(), timeout=10.0)
            await getattr(session.browser, "close", lambda: None)()
            return True
        except Exception as exc:
            log.warning("bd_cdp_pool.health_check_failed", error=str(exc))
            return False

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _get_or_create_session(self) -> BDSession:
        """Get a healthy session from the pool, creating one if needed."""
        async with self._pool_lock:
            # Find a healthy available session
            for session in self._sessions:
                if session.is_healthy():
                    return session

        # No healthy session found — create a new one
        session = await self._create_session()
        async with self._pool_lock:
            # Cap pool size to max_connections
            if len(self._sessions) < self._max_connections:
                self._sessions.append(session)
                BD_BROWSER_POOL_SIZE.set(len(self._sessions))
        return session

    async def _create_session(self) -> BDSession:
        """Create a new CDP browser session connected to BD Cloud Browser."""
        from playwright.async_api import async_playwright

        # Build CDP URL transiently — never stored as instance attribute
        cdp_url = build_browser_cdp_url(
            customer_id=self._customer_id,
            zone=self._zone,
            password=self._password,
        )

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
        except Exception:
            await playwright.stop()
            raise

        log.debug("bd_cdp_pool.session_created")
        return BDSession(browser=browser)

    async def _recycle_session(self, session: BDSession) -> None:
        """Replace an overused session with a fresh one."""
        try:
            async with self._pool_lock:
                if session in self._sessions:
                    self._sessions.remove(session)
                    BD_BROWSER_POOL_SIZE.set(len(self._sessions))

            try:
                await asyncio.wait_for(
                    getattr(session.browser, "close", lambda: None)(),
                    timeout=5.0,
                )
            except Exception:
                pass

            new_session = await self._create_session()
            async with self._pool_lock:
                if len(self._sessions) < self._max_connections:
                    self._sessions.append(new_session)
                    BD_BROWSER_POOL_SIZE.set(len(self._sessions))
            log.debug("bd_cdp_pool.session_recycled")
        except Exception as exc:
            log.warning("bd_cdp_pool.recycle_failed", error=str(exc))

    async def _maintenance_loop(self) -> None:
        """Background loop: health-check sessions and replace dead ones."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(60.0)
                if self._shutdown_event.is_set():
                    break

                async with self._pool_lock:
                    dead = [s for s in self._sessions if not s.is_healthy()]

                for session in dead:
                    log.warning("bd_cdp_pool.dead_session_detected", request_count=session.request_count)
                    await self._recycle_session(session)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("bd_cdp_pool.maintenance_error", error=str(exc))


# ── Module-level singleton (initialized at app startup) ───────────────────────

_bd_cdp_pool: BDCDPPool | None = None


def get_bd_cdp_pool() -> BDCDPPool | None:
    """Return the global BD CDP pool (None if not initialized)."""
    return _bd_cdp_pool


async def init_bd_cdp_pool(
    customer_id: str,
    zone: str,
    password: str,
    max_connections: int = 5,
) -> BDCDPPool:
    """
    Initialize the global BD CDP pool. Call from application lifespan hook.

    Args:
        customer_id: BD customer ID.
        zone: BD zone name for scraping browser.
        password: Zone password.
        max_connections: Maximum concurrent connections (match BD zone limit).

    Returns:
        The initialized pool singleton.
    """
    global _bd_cdp_pool
    pool = BDCDPPool(max_connections=max_connections)
    await pool.initialize(customer_id=customer_id, zone=zone, password=password)
    _bd_cdp_pool = pool
    return pool


async def shutdown_bd_cdp_pool() -> None:
    """Gracefully shutdown the global BD CDP pool. Call from lifespan cleanup."""
    global _bd_cdp_pool
    if _bd_cdp_pool is not None:
        await _bd_cdp_pool.shutdown()
        _bd_cdp_pool = None
