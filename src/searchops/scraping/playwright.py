"""
Playwright Headless Browser Scraper.

Implements `IScraper` port using Playwright for dynamic SPA / JS rendering.
"""

from __future__ import annotations

import structlog

from searchops.config.settings import Settings, get_settings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult

log = structlog.get_logger(__name__)


class PlaywrightScraper(IScraper):
    """Playwright Headless Browser implementation of IScraper."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.scraping

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape webpage using Playwright async browser session."""
        log.info("Scraping with Playwright", url=request.url)
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(request.url, timeout=request.timeout_seconds * 1000)

                html_content = await page.content()
                title = await page.title()
                final_url = page.url
                await browser.close()

                return ScrapeResult(
                    url=request.url,
                    final_url=final_url,
                    status_code=200,
                    html=html_content,
                    title=title,
                    scrape_mode_used=ScrapeMode.PLAYWRIGHT,
                )
        except Exception as exc:
            log.error("Playwright scraping failed", url=request.url, error=str(exc))
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.PLAYWRIGHT,
                metadata={"error": str(exc)},
            )

    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 5
    ) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently via Playwright."""
        import asyncio

        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        tasks = [_bounded(req) for req in requests]
        return list(await asyncio.gather(*tasks))

    async def health_check(self) -> bool:
        """Return True if Playwright dependency is available."""
        try:
            import playwright  # type: ignore[import]
            return True
        except ImportError:
            return False

