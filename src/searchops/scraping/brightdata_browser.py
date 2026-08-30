"""
Bright Data Cloud Scraping Browser Scraper — Premium Tier (Priority 6).

Connects Playwright to Bright Data's cloud-hosted Chromium instances via
Chrome DevTools Protocol (CDP) over WebSocket. This offloads all browser
resource usage (RAM, CPU, IP reputation) to Bright Data's infrastructure.

Security improvements over original implementation:
  - CDP credentials never stored as a plain string instance attribute
  - Credentials built transiently inside method scope via bd_auth module
  - asyncio imported at module top
  - wait_until="networkidle" + 120s timeout replaced with domcontentloaded + 30s

Architecture improvements:
  - Uses BDCDPPool for connection reuse — no new WebSocket per request
  - Pool is injected as dependency (testable, replaceable)
  - Circuit breaker checked before every scrape via pipeline integration

BD docs: https://docs.brightdata.com/scraping-automation/scraping-browser
"""

from __future__ import annotations

import asyncio
import base64
import time

import structlog

from searchops.config.subsystems.scraping import ScrapingSettings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.bd_auth import (
    build_browser_cdp_url,
    mask_bd_credential,
)
from searchops.scraping.bd_browser_pool import BDCDPPool, get_bd_cdp_pool
from searchops.scraping.bd_metrics import (
    BD_HEALTH_CHECK_FAILURES,
    record_bd_request,
)
from searchops.scraping.content_pruner import get_content_pruner

log = structlog.get_logger(__name__)

_BD_CDP_HOST = "brd.superproxy.io"
_BD_CDP_PORT = 9222


class BrightDataBrowserScraper(IScraper):
    """
    Cloud Scraping Browser via Bright Data CDP — Premium Tier (Priority 6).

    Activated in the ScrapingPipeline after Firecrawl (priority 5) fails
    with an ACCESS failure (403/429/503) — NOT on 404 content failures.

    Uses BDCDPPool for persistent connection reuse rather than creating a
    new WebSocket per request. Pool must be initialized before first use.
    """

    def __init__(self, cfg: ScrapingSettings, pool: BDCDPPool | None = None) -> None:
        if not cfg.brightdata_customer_id or not cfg.brightdata_zone_password:
            raise ValueError(
                "BrightDataBrowserScraper requires BRIGHTDATA_CUSTOMER_ID "
                "and BRIGHTDATA_ZONE_PASSWORD to be configured."
            )
        # Store credentials separately — NEVER build and store a CDP URL string here
        self._customer_id: str = cfg.brightdata_customer_id
        self._zone: str = cfg.brightdata_zone_scraping_browser
        self._password: str = cfg.brightdata_zone_password.get_secret_value()
        self._timeout: int = cfg.request_timeout
        self._pruner = get_content_pruner()
        # Pool injected or resolved from global singleton
        self._pool: BDCDPPool | None = pool or get_bd_cdp_pool()

    # ── IScraper protocol ────────────────────────────────────────────────────

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape a dynamic page via Bright Data Cloud Scraping Browser CDP."""
        start = time.perf_counter()
        log.info("bd_browser.scrape", url=request.url)

        # Use pool if available, otherwise fall back to direct connection
        if self._pool is not None:
            return await self._scrape_via_pool(request, start)
        return await self._scrape_direct(request, start)

    async def _scrape_via_pool(self, request: ScrapeRequest, start: float) -> ScrapeResult:
        """Scrape using a session from the persistent CDP pool."""
        try:
            async with self._pool.acquire() as session:  # type: ignore[union-attr]
                page = None
                try:
                    page = await session.browser.new_page()  # type: ignore[attr-defined]
                    return await self._do_scrape(page, request, start)
                finally:
                    if page is not None:
                        try:
                            await page.close()
                        except Exception:
                            pass
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            log.error("bd_browser.scrape: pool acquire timeout", url=request.url)
            record_bd_request("browser", "failure", elapsed_ms / 1000)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=408,
                scrape_mode_used=ScrapeMode.BD_BROWSER,
                duration_ms=elapsed_ms,
                metadata={"error": "CDP pool acquire timeout"},
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            safe_err = mask_bd_credential(str(exc))
            log.error("bd_browser.scrape: exception", url=request.url, error=safe_err)
            record_bd_request("browser", "failure", elapsed_ms / 1000)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.BD_BROWSER,
                duration_ms=elapsed_ms,
                metadata={"error": safe_err},
            )

    async def _scrape_direct(self, request: ScrapeRequest, start: float) -> ScrapeResult:
        """Fallback: direct CDP connection when pool is not initialized."""
        from playwright.async_api import async_playwright

        browser = None
        page = None
        # CDP URL built transiently here — never stored as self attribute
        cdp_url = build_browser_cdp_url(
            customer_id=self._customer_id,
            zone=self._zone,
            password=self._password,
            host=_BD_CDP_HOST,
            port=_BD_CDP_PORT,
        )

        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                page = await browser.new_page()
                result = await self._do_scrape(page, request, start)
                await browser.close()
                return result
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            safe_err = mask_bd_credential(str(exc))
            log.error("bd_browser.scrape: direct exception", url=request.url, error=safe_err)
            record_bd_request("browser", "failure", elapsed_ms / 1000)
            if browser is not None:
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
                metadata={"error": safe_err},
            )

    async def _do_scrape(self, page: object, request: ScrapeRequest, start: float) -> ScrapeResult:
        """Core page scraping logic, shared between pool and direct paths."""
        # Navigate — BD automatically handles CAPTCHAs and bot checks
        # Use domcontentloaded (not networkidle) with a reasonable 30s timeout
        page_timeout_ms = min(self._timeout * 1000, 30_000)  # Cap at 30s
        await page.goto(  # type: ignore[attr-defined]
            request.url,
            wait_until="domcontentloaded",
            timeout=page_timeout_ms,
        )

        # Wait for optional CSS selector before capturing
        if request.wait_for_selector:
            await page.wait_for_selector(  # type: ignore[attr-defined]
                request.wait_for_selector,
                timeout=15_000,
            )

        html = await page.content()  # type: ignore[attr-defined]
        title = await page.title()  # type: ignore[attr-defined]
        final_url = page.url  # type: ignore[attr-defined]

        # Capture screenshot if requested
        screenshot_b64: str | None = None
        if request.take_screenshot:
            screenshot_bytes = await page.screenshot(type="png", full_page=True)  # type: ignore[attr-defined]
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

        elapsed_s = time.perf_counter() - start
        elapsed_ms = elapsed_s * 1000
        markdown = self._pruner.prune(html) if request.extract_markdown else None

        log.info(
            "bd_browser.scrape: success",
            url=request.url,
            elapsed_ms=round(elapsed_ms, 1),
        )
        record_bd_request("browser", "success", elapsed_s, cost_usd=0.01)
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

    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 3
    ) -> list[ScrapeResult]:
        """
        Scrape multiple URLs concurrently via BD Cloud Browser.

        Concurrency is bounded by the pool semaphore — no need for an extra
        semaphore here when a pool is active. Max concurrency = pool size.
        """
        tasks = [self.scrape(req) for req in requests]
        return list(await asyncio.gather(*tasks))

    async def health_check(self) -> bool:
        """
        Verify BD Scraping Browser is reachable.

        Uses the pool health check if pool is available;
        otherwise attempts a transient direct connection.
        """
        if self._pool is not None:
            return await self._pool.health_check()

        from playwright.async_api import async_playwright
        cdp_url = build_browser_cdp_url(
            customer_id=self._customer_id,
            zone=self._zone,
            password=self._password,
            host=_BD_CDP_HOST,
            port=_BD_CDP_PORT,
        )
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                await browser.close()
            return True
        except Exception as exc:
            safe_err = mask_bd_credential(str(exc))
            log.warning("bd_browser.health_check: failed", error=safe_err)
            BD_HEALTH_CHECK_FAILURES.labels(tier="browser").inc()
            return False


def build_bd_browser(
    cfg: ScrapingSettings,
    pool: BDCDPPool | None = None,
) -> BrightDataBrowserScraper | None:
    """
    Factory: returns a configured BrightDataBrowserScraper or None if
    credentials are not set (graceful no-op for free-tier deployments).
    """
    if cfg.brightdata_customer_id and cfg.brightdata_zone_password:
        try:
            return BrightDataBrowserScraper(cfg, pool=pool)
        except Exception as exc:
            log.warning("bd_browser: failed to build scraper", error=str(exc))
    return None
