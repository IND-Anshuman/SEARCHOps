import structlog
from typing import Any

log = structlog.get_logger(__name__)

class BrowserManager:
    """Manages a persistent, reusable Chromium browser context pool for scraping."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    async def initialize(self) -> None:
        """Start Playwright and launch browser instance if not already running."""
        if self._browser is not None:
            return

        try:
            from playwright.async_api import async_playwright
            log.info("Starting persistent Playwright browser instance")
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"]
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
        except Exception as e:
            log.error("Failed to initialize Playwright browser", error=str(e))
            raise e

    async def get_page(self) -> Any:
        """Allocate a page from the persistent context."""
        await self.initialize()
        if not self._context:
            raise RuntimeError("Browser context is not initialized")
        return await self._context.new_page()

    async def release_page(self, page: Any) -> None:
        """Close page instance safely to free memory."""
        try:
            if page:
                await page.close()
        except Exception:
            pass

    async def close_browser(self) -> None:
        """Close browser and context pools completely."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            log.info("Closed persistent Playwright browser instance")


# Global singleton browser manager instance
browser_manager = BrowserManager()
