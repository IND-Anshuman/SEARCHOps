"""
Bright Data Web Unlocker Scraper — Premium Tier (Priority 4).

Routes requests through Bright Data's residential proxy network with
automated CAPTCHA solving, fingerprint spoofing, and TLS mimicry.
Success rate: ~99.9% on targets that block all free-tier scrapers.

Security improvements over original implementation:
  - TLS verification ENABLED (verify=False removed)
  - Credentials separated from proxy URL via BrightDataProxyAuth
  - Passwords never appear in log lines, exception strings, or OTEL spans
  - asyncio imported at module top

BD docs: https://docs.brightdata.com/scraping-automation/web-unlocker
"""

from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from searchops.config.subsystems.scraping import ScrapingSettings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.bd_auth import (
    BrightDataProxyAuth,
    build_unlocker_proxy,
    get_ssl_context,
    mask_bd_credential,
)
from searchops.scraping.bd_metrics import (
    BD_HEALTH_CHECK_FAILURES,
    record_bd_request,
)
from searchops.scraping.content_pruner import get_content_pruner

log = structlog.get_logger(__name__)

_BD_PROXY_HOST = "brd.superproxy.io"
_BD_PROXY_PORT = 22225
_BD_HEALTH_URL = "https://geo.brdtest.com/mygeo.json"


class BrightDataUnlockerScraper(IScraper):
    """
    Web Unlocker — CAPTCHA bypass via Bright Data residential proxy network.

    Activated as priority-4 tier in the ScrapingPipeline after local Playwright
    fails with HTTP 403/429/503 or a CAPTCHA detection response.

    Credentials are passed via httpx.Auth — never embedded in the proxy URL.
    TLS verification is enabled against the system CA bundle (certifi).
    """

    def __init__(self, cfg: ScrapingSettings) -> None:
        if not cfg.brightdata_customer_id or not cfg.brightdata_zone_password:
            raise ValueError(
                "BrightDataUnlockerScraper requires BRIGHTDATA_CUSTOMER_ID "
                "and BRIGHTDATA_ZONE_PASSWORD to be configured."
            )
        # Store credentials separately — never as a composite URL string
        self._customer_id: str = cfg.brightdata_customer_id
        self._zone: str = cfg.brightdata_zone_unlocker
        self._password: str = cfg.brightdata_zone_password.get_secret_value()
        self._timeout: int = cfg.request_timeout
        self._pruner = get_content_pruner()
        self._ssl_context = get_ssl_context()

    # ── Properties (no credential exposure) ─────────────────────────────────

    @property
    def _proxy_config(self) -> tuple[str, BrightDataProxyAuth]:
        """Build proxy URL + auth object transiently. Never stored."""
        return build_unlocker_proxy(
            customer_id=self._customer_id,
            zone=self._zone,
            password=self._password,
            host=_BD_PROXY_HOST,
            port=_BD_PROXY_PORT,
        )

    # ── IScraper protocol ────────────────────────────────────────────────────

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Fetch URL through Bright Data Web Unlocker proxy."""
        start = time.perf_counter()
        log.info("bd_unlocker.scrape", url=request.url)

        proxy_url, proxy_auth = self._proxy_config

        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                auth=proxy_auth,
                verify=self._ssl_context,   # TLS verification ENABLED
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

            elapsed_s = time.perf_counter() - start
            elapsed_ms = elapsed_s * 1000

            if resp.status_code != 200:
                log.warning(
                    "bd_unlocker.scrape: non-200 response",
                    url=request.url,
                    status=resp.status_code,
                    elapsed_ms=round(elapsed_ms, 1),
                )
                record_bd_request("unlocker", "failure", elapsed_s, trigger_status=str(resp.status_code))
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
                elapsed_ms=round(elapsed_ms, 1),
                content_bytes=len(resp.content),
            )
            record_bd_request("unlocker", "success", elapsed_s, cost_usd=0.001)
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
            # Mask any credential that may appear in the exception message
            safe_err = mask_bd_credential(str(exc))
            log.error("bd_unlocker.scrape: exception", url=request.url, error=safe_err)
            record_bd_request("unlocker", "failure", elapsed_ms / 1000)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.BD_UNLOCKER,
                duration_ms=elapsed_ms,
                metadata={"error": safe_err},
            )

    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 5
    ) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently via BD Unlocker."""
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def health_check(self) -> bool:
        """Verify proxy connectivity by hitting Bright Data's geo endpoint."""
        proxy_url, proxy_auth = self._proxy_config
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                auth=proxy_auth,
                verify=self._ssl_context,
                timeout=10.0,
            ) as client:
                resp = await client.get(_BD_HEALTH_URL)
                return resp.status_code == 200
        except Exception as exc:
            safe_err = mask_bd_credential(str(exc))
            log.warning("bd_unlocker.health_check: failed", error=safe_err)
            BD_HEALTH_CHECK_FAILURES.labels(tier="unlocker").inc()
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
