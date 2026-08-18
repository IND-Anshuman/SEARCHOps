"""
Bright Data Web Unlocker Scraper — Premium Tier (Tier 1.5).

Routes requests through Bright Data's residential proxy network with
automated CAPTCHA solving, fingerprint spoofing, and TLS mimicry.
Success rate: ~99.9% on targets that block all free-tier scrapers.

Proxy format:
    http://brd-customer-<customer_id>-zone-<zone>:<password>@brd.superproxy.io:22225

BD docs: https://docs.brightdata.com/scraping-automation/web-unlocker
"""

from __future__ import annotations

import structlog
import time

import httpx

from searchops.config.subsystems.scraping import ScrapingSettings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.content_pruner import get_content_pruner

log = structlog.get_logger(__name__)

# BD superproxy host for all zone products
_BD_PROXY_HOST = "brd.superproxy.io"
_BD_PROXY_PORT = 22225
_BD_HEALTH_URL = "https://geo.brdtest.com/mygeo.json"


class BrightDataUnlockerScraper(IScraper):
    """
    Web Unlocker — CAPTCHA bypass via Bright Data residential proxy network.

    Activated as Tier 1.5 in the ScrapingPipeline after local Playwright
    fails with HTTP 403/429/503 or a CAPTCHA detection response.
    """

    def __init__(self, cfg: ScrapingSettings) -> None:
        if not cfg.brightdata_customer_id or not cfg.brightdata_zone_password:
            raise ValueError(
                "BrightDataUnlockerScraper requires BRIGHTDATA_CUSTOMER_ID "
                "and BRIGHTDATA_ZONE_PASSWORD to be configured."
            )
        cid = cfg.brightdata_customer_id
        zone = cfg.brightdata_zone_unlocker
        pwd = cfg.brightdata_zone_password.get_secret_value()
        # httpx proxy URL with auth embedded
        self._proxy_url = f"http://brd-customer-{cid}-zone-{zone}:{pwd}@{_BD_PROXY_HOST}:{_BD_PROXY_PORT}"
        self._timeout = cfg.request_timeout
        self._pruner = get_content_pruner()

    # ------------------------------------------------------------------ #
    #  IScraper protocol                                                   #
    # ------------------------------------------------------------------ #

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Fetch URL through Bright Data Web Unlocker proxy."""
        start = time.perf_counter()
        log.info("bd_unlocker.scrape", url=request.url)

        try:
            # BD handles SSL termination — verify=False required for proxy MITM
            async with httpx.AsyncClient(
                proxy=self._proxy_url,
                verify=False,
                timeout=self._timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.get(
                    request.url,
                    headers={
                        **request.headers,
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                    },
                )

            elapsed_ms = (time.perf_counter() - start) * 1000

            if resp.status_code != 200:
                log.warning(
                    "bd_unlocker.scrape: non-200 response",
                    url=request.url,
                    status=resp.status_code,
                    elapsed_ms=elapsed_ms,
                )
                return ScrapeResult(
                    url=request.url,
                    final_url=str(resp.url),
                    status_code=resp.status_code,
                    scrape_mode_used=ScrapeMode.BD_UNLOCKER,
                    duration_ms=elapsed_ms,
                    metadata={"error": f"BD Unlocker HTTP {resp.status_code}"},
                )

            html = resp.text
            markdown = self._pruner.prune(html) if request.extract_markdown else None

            log.info(
                "bd_unlocker.scrape: success",
                url=request.url,
                elapsed_ms=elapsed_ms,
                content_bytes=len(resp.content),
            )
            return ScrapeResult(
                url=request.url,
                final_url=str(resp.url),
                status_code=200,
                html=html,
                markdown=markdown,
                scrape_mode_used=ScrapeMode.BD_UNLOCKER,
                duration_ms=elapsed_ms,
                metadata={"proxy": "brightdata_unlocker"},
            )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            log.error("bd_unlocker.scrape: exception", url=request.url, error=str(exc))
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.BD_UNLOCKER,
                duration_ms=elapsed_ms,
                metadata={"error": str(exc)},
            )

    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 5
    ) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently via BD Unlocker."""
        import asyncio

        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def health_check(self) -> bool:
        """Verify proxy connectivity by hitting Bright Data's geo endpoint."""
        try:
            async with httpx.AsyncClient(
                proxy=self._proxy_url,
                verify=False,
                timeout=10.0,
            ) as client:
                resp = await client.get(_BD_HEALTH_URL)
                return resp.status_code == 200
        except Exception as exc:
            log.warning("bd_unlocker.health_check: failed", error=str(exc))
            return False


def build_bd_unlocker(cfg: ScrapingSettings) -> BrightDataUnlockerScraper | None:
    """
    Factory: returns a configured BrightDataUnlockerScraper or None if
    credentials are not set (graceful no-op for free-tier deployments).
    """
    if cfg.brightdata_customer_id and cfg.brightdata_zone_password:
        try:
            return BrightDataUnlockerScraper(cfg)
        except Exception as exc:
            log.warning("bd_unlocker: failed to build scraper", error=str(exc))
    return None
