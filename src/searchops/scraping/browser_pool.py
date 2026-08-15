"""
Browser Pool Manager for Playwright.

Provides pre-warmed browser instances with reusable contexts to eliminate
cold start latency (300-1200ms) and enable 10-50 concurrent contexts per browser.
"""

from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import structlog

from searchops.scraping.playwright import PlaywrightScraper

log = structlog.get_logger(__name__)


@dataclass
class PoolConfig:
    """Configuration for browser pool."""

    pool_size: int = 5
    """Number of browser instances to maintain."""

    max_contexts_per_browser: int = 10
    """Maximum concurrent contexts per browser instance."""

    context_timeout_seconds: float = 60.0
    """Timeout for context acquisition."""

    browser_idle_timeout_seconds: float = 300.0
    """Close idle browsers after this duration."""

    request_before_recycle: int = 100
    """Recycle browser after this many requests to prevent memory leaks."""

    headless: bool = True
    """Run browsers in headless mode."""

    # Shared memory configuration to prevent browser crashes
    # Chromium needs /dev/shm to be at least 1GB in containerized environments
    dev_shm_size: str = "2g"
    """Size of /dev/shm shared memory. Set to '2g' for containers, '256m' for local."""

    disable_dev_shm_usage: bool = False
    """If True, disables /dev/shm usage (use when shm is not available)."""

    args: list[str] = field(default_factory=lambda: [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",  # Fallback if /dev/shm is small
        "--disable-gpu",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
    ])
    """Additional browser arguments for stealth and stability."""


@dataclass
class BrowserInstance:
    """Represents a single browser instance with its contexts."""

    browser: Any  # playwright.async_api.Browser
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    request_count: int = 0
    contexts: list[Any] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        """Check if browser is still connected and responsive."""
        try:
            return self.browser.is_connected()
        except Exception:
            return False


class BrowserContext:
    """A wrapper around Playwright browser context with automatic recycling."""

    def __init__(
        self,
        context: Any,
        instance: BrowserInstance,
        pool: BrowserPool,
    ) -> None:
        self._context = context
        self._instance = instance
        self._pool = pool
        self._acquired_at = asyncio.get_event_loop().time()
        self._page: Any | None = None

    @property
    def context(self) -> Any:
        """Access the underlying Playwright context."""
        return self._context

    @property
    async def new_page(self) -> Any:
        """Create a new page within this context."""
        if self._page is None or not self._page.is_closed():
            self._page = await self._context.new_page()
        return self._page

    async def close(self) -> None:
        """Release this context back to the pool."""
        await self._pool.release(self)


class BrowserPool:
    """
    Manages a pool of pre-warmed Playwright browser instances.

    Features:
    - Pre-warmed browsers eliminate cold start latency (300-1200ms -> <50ms)
    - Context pooling supports 10-50 concurrent contexts per browser
    - Automatic recycling after N requests to prevent memory leaks
    - Health checking and automatic recovery
    - Thread-safe async operations

    Example:
        async with browser_pool.acquire() as ctx:
            page = await ctx.new_page()
            await page.goto("https://example.com")
            content = await page.content()
    """

    def __init__(self, config: PoolConfig | None = None) -> None:
        self.config = config or PoolConfig()
        self._browsers: list[BrowserInstance] = []
        self._available_contexts: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._shutdown = False
        self._total_acquired = 0
        self._cleanup_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize the pool by pre-warming browsers."""
        log.info(
            "Initializing browser pool",
            pool_size=self.config.pool_size,
            max_contexts=self.config.max_contexts_per_browser,
        )

        # Pre-warm browsers
        for _ in range(self.config.pool_size):
            await self._launch_browser()

        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        log.info("Browser pool initialized", browsers=len(self._browsers))

    async def _launch_browser(self) -> BrowserInstance:
        """Launch a new browser instance."""
        try:
            from playwright.async_api import async_playwright

            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=self.config.headless,
                args=self.config.args,
                ignore_default_args=["--enable-automation"],  # Stealth mode
            )

            # Store playwright reference to prevent garbage collection
            instance = BrowserInstance(browser=browser)
            instance.playwright_ref = playwright  # Keep reference alive

            self._browsers.append(instance)
            log.debug("Launched new browser", total_browsers=len(self._browsers))

            return instance
        except Exception as e:
            log.error("Failed to launch browser", error=str(e))
            raise

    async def _get_available_instance(self) -> BrowserInstance:
        """Get a browser instance with available capacity."""
        for instance in self._browsers:
            if instance.is_healthy and len(instance.contexts) < self.config.max_contexts_per_browser:
                return instance

        # All browsers at capacity, launch new one if under pool limit
        if len(self._browsers) < self.config.pool_size:
            return await self._launch_browser()

        # Wait for context to be released
        raise RuntimeError("Browser pool at capacity")

    @asynccontextmanager
    async def acquire(self, timeout: float | None = None) -> AsyncGenerator[BrowserContext, None]:
        """
        Acquire a browser context from the pool.

        This is the main entry point for using the pool.
        Automatically releases context back to pool on exit.

        Example:
            async with pool.acquire() as ctx:
                page = await ctx.new_page()
                await page.goto("https://example.com")
        """
        if self._shutdown:
            raise RuntimeError("Browser pool is shut down")

        timeout = timeout or self.config.context_timeout_seconds

        # Try to get from available queue first
        ctx = None
        try:
            ctx = await asyncio.wait_for(
                self._available_contexts.get(),
                timeout=timeout,
            )
            # Verify context is still valid
            if ctx._context.pages:  # Context still has pages, good
                self._total_acquired += 1
                ctx._instance.request_count += 1
                log.debug(
                    "Reusing browser context",
                    instance_requests=ctx._instance.request_count,
                    total_acquired=self._total_acquired,
                )
                yield ctx
                return
        except asyncio.TimeoutError:
            pass  # Fall through to create new context
        except Exception as e:
            log.warning("Failed to reuse context, creating new one", error=str(e))

        # Create new context
        async with self._lock:
            instance = await self._get_available_instance()

        try:
            context = await instance.browser.new_context(
                # Stealth options
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["geolocation", "notifications"],
            )

            ctx = BrowserContext(context, instance, self)
            instance.contexts.append(context)
            self._total_acquired += 1
            instance.request_count += 1

            log.debug(
                "Created new browser context",
                total_browsers=len(self._browsers),
                contexts_on_instance=len(instance.contexts),
                request_count=instance.request_count,
            )

            yield ctx

        finally:
            # Release back to pool or close
            await self._pool_context(ctx)

    async def _pool_context(self, ctx: BrowserContext | None) -> None:
        """Return a context to the pool or close it."""
        if ctx is None:
            return

        instance = ctx._instance

        # Check if should recycle this browser
        if instance.request_count >= self.config.request_before_recycle:
            await self._recycle_instance(instance)
            return

        # Check if context is still valid
        try:
            if ctx._context.pages:
                # Return to available queue
                await self._available_contexts.put(ctx)
                log.debug("Returned context to pool")
                return
        except Exception:
            pass

        # Context invalid, remove from tracking
        if instance in self._browsers:
            instance.contexts = [c for c in instance.contexts if c != ctx._context]

    async def _recycle_instance(self, instance: BrowserInstance) -> None:
        """Recycle a browser instance (close and relaunch)."""
        log.info(
            "Recycling browser instance",
            request_count=instance.request_count,
            threshold=self.config.request_before_recycle,
        )

        try:
            # Close all contexts
            for context in instance.contexts:
                try:
                    await context.close()
                except Exception:
                    pass

            # Close browser
            if instance.is_healthy:
                await instance.browser.close()

            # Remove from pool
            if instance in self._browsers:
                self._browsers.remove(instance)

            # Launch new one
            async with self._lock:
                await self._launch_browser()

            log.info("Browser instance recycled successfully")

        except Exception as e:
            log.error("Failed to recycle browser instance", error=str(e))

    async def release(self, ctx: BrowserContext) -> None:
        """Explicitly release a context back to the pool."""
        await self._pool_context(ctx)

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of idle browsers and stale contexts."""
        while not self._shutdown:
            try:
                await asyncio.sleep(60)  # Check every minute

                async with self._lock:
                    # Remove closed browsers
                    self._browsers = [b for b in self._browsers if b.is_healthy]

                    # Maintain minimum pool size
                    while len(self._browsers) < self.config.pool_size:
                        await self._launch_browser()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Cleanup loop error", error=str(e))

    async def shutdown(self) -> None:
        """Gracefully shut down the browser pool."""
        log.info("Shutting down browser pool")
        self._shutdown = True

        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all browsers
        for instance in self._browsers:
            try:
                if instance.is_healthy:
                    await instance.browser.close()
            except Exception as e:
                log.warning("Error closing browser", error=str(e))

        self._browsers.clear()

        # Drain queue
        while not self._available_contexts.empty():
            try:
                self._available_contexts.get_nowait()
            except asyncio.QueueEmpty:
                break

        log.info("Browser pool shut down complete")

    @property
    def stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        return {
            "total_browsers": len(self._browsers),
            "available_contexts": self._available_contexts.qsize(),
            "total_acquired": self._total_acquired,
            "pool_size": self.config.pool_size,
            "max_contexts_per_browser": self.config.max_contexts_per_browser,
        }


# Global browser pool instance
_browser_pool: BrowserPool | None = None


def get_browser_pool(config: PoolConfig | None = None) -> BrowserPool:
    """Get or create the global browser pool instance."""
    global _browser_pool
    if _browser_pool is None:
        _browser_pool = BrowserPool(config)
    return _browser_pool


async def initialize_browser_pool(config: PoolConfig | None = None) -> BrowserPool:
    """Initialize the global browser pool."""
    pool = get_browser_pool(config)
    await pool.initialize()
    return pool


async def shutdown_browser_pool() -> None:
    """Shutdown the global browser pool."""
    global _browser_pool
    if _browser_pool:
        await _browser_pool.shutdown()
        _browser_pool = None