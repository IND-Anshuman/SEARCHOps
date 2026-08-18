"""
Bright Data Cloud Scraping Browser Scraper — Premium Tier (Tier 2.5).

Connects Playwright to Bright Data's cloud-hosted Chromium instances via
Chrome DevTools Protocol (CDP) over WebSocket. This offloads all browser
resource usage (RAM, CPU, IP reputation) to Bright Data's infrastructure.

Ideal for:
- Pages requiring complex JS execution after Firecrawl fails
- Dynamic SPA content with multi-step user interactions
- Sites with sophisticated browser fingerprinting checks
- Scraping at scale without consuming local machine resources

CDP WebSocket endpoint format:
    wss://brd-customer-<customer_id>-zone-<zone>:<password>@brd.superproxy.io:9222

BD docs: https://docs.brightdata.com/scraping-automation/scraping-browser
"""

from __future__ import annotations

import base64
import structlog
import time

from playwright.async_api import async_playwright

from searchops.config.subsystems.scraping import ScrapingSettings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.content_pruner import get_content_pruner

log = structlog.get_logger(__name__)

_BD_CDP_HOST = "brd.superproxy.io"
_BD_CDP_PORT = 9222


class BrightDataBrowserScraper(IScraper):
    """
    Cloud Scraping Browser via Bright Data CDP — Premium Tier (Tier 2.5).

    Activated in the ScrapingPipeline after Firecrawl (Tier 2) fails.
    Playwright connects to a remote cloud browser over WebSocket rather than
    launching a local Chromium process, eliminating local RAM/CPU overhead.
    """

    def __init__(self, cfg: ScrapingSettings) -> None:
        if not cfg.brightdata_customer_id or not cfg.brightdata_zone_password:
            raise ValueError(
                "BrightDataBrowserScraper requires BRIGHTDATA_CUSTOMER_ID "
                "and BRIGHTDATA_ZONE_PASSWORD to be configured."
            )
        cid = cfg.brightdata_customer_id
        zone = cfg.brightdata_zone_scraping_browser
        pwd = cfg.brightdata_zone_password.get_secret_value()
        # CDP WebSocket URL with embedded auth
        self._cdp_url = (
            f"wss://brd-customer-{cid}-zone-{zone}:{pwd}"
            f"@{_BD_CDP_HOST}:{_BD_CDP_PORT}"
        )
        self._pruner = get_content_pruner()

    # ------------------------------------------------------------------ #
    #  IScraper protocol                                                   #
    # ------------------------------------------------------------------ #

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape a dynamic page via Bright Data Cloud Scraping Browser CDP."""
        start = time.perf_counter()
        log.info("bd_browser.scrape", url=request.url)

        browser = None
        try:
            async with async_playwright() as p:
                # Connect to Bright Data's cloud Chromium over CDP WebSocket
                browser = await p.chromium.connect_over_cdp(self._cdp_url)
                page = await browser.new_page()

                # Navigate — BD automatically handles CAPTCHAs and bot checks
                await page.goto(
                    request.url,
                    wait_until="networkidle",
                    timeout=120_000,  # 120s for heavy pages
                )

                # Wait for optional CSS selector before capturing
                if request.wait_for_selector:
                    await page.wait_for_selector(
                        request.wait_for_selector, timeout=15_000
                    )

                html = await page.content()
                title = await page.title()
                final_url = page.url

                # Capture screenshot if requested
                screenshot_b64: str | None = None
                if request.take_screenshot:
                    screenshot_bytes = await page.screenshot(type="png", full_page=True)
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

                await browser.close()
                browser = None

            elapsed_ms = (time.perf_counter() - start) * 1000
            markdown = self._pruner.prune(html) if request.extract_markdown else None

            log.info(
                "bd_browser.scrape: success",
                url=request.url,
                elapsed_ms=elapsed_ms,
            )
            return ScrapeResult(
                url=request.url,
                final_url=final_url,
                status_code=200,
                html=html,
                markdown=markdown,
                title=title,
                screenshot_base64=screenshot_b64,
                scrape_mode_used=ScrapeMode.BD_BROWSER,
                duration_ms=elapsed_ms,
                metadata={"proxy": "brightdata_scraping_browser"},
            )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            log.error("bd_browser.scrape: exception", url=request.url, error=str(exc))
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.BD_BROWSER,
                duration_ms=elapsed_ms,
                metadata={"error": str(exc)},
            )

    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 3
    ) -> list[ScrapeResult]:
        """
        Scrape multiple URLs concurrently via BD Cloud Browser.

        Max concurrency defaults to 3 (BD allocates one cloud browser session
        per concurrent connection — keep low to avoid exceeding zone limits).
        """
        import asyncio

        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def health_check(self) -> bool:
        """
        Verify BD Scraping Browser is reachable by attempting a CDP connection.
        Opens and immediately closes a browser instance.
        """
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(self._cdp_url)
                await browser.close()
            return True
        except Exception as exc:
            log.warning("bd_browser.health_check: failed", error=str(exc))
            return False


def build_bd_browser(cfg: ScrapingSettings) -> BrightDataBrowserScraper | None:
    """
    Factory: returns a configured BrightDataBrowserScraper or None if
    credentials are not set (graceful no-op for free-tier deployments).
    """
    if cfg.brightdata_customer_id and cfg.brightdata_zone_password:
        try:
            return BrightDataBrowserScraper(cfg)
        except Exception as exc:
            log.warning("bd_browser: failed to build scraper", error=str(exc))
    return None
