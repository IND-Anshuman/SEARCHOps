"""
Firecrawl Scraper Implementation.

Implements `IScraper` port using Firecrawl API / MCP server.
"""

from __future__ import annotations

import httpx
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.core.exceptions.infrastructure import ScrapingError
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult

log = structlog.get_logger(__name__)


class FirecrawlScraper(IScraper):
    """Firecrawl API implementation of IScraper."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.scraping
        self.api_key = self.settings.firecrawl_api_key.get_secret_value() if self.settings.firecrawl_api_key else None
        self.api_url = self.settings.firecrawl_api_url

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape a webpage using Firecrawl API."""
        if not self.api_key:
            log.warning("Firecrawl API key not set — returning error status")
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=401,
                markdown=None,
                scrape_mode_used=ScrapeMode.FIRECRAWL,
                metadata={"error": "Firecrawl API key is not configured"},
            )

        log.info("Scraping with Firecrawl", url=request.url)
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                resp = await client.post(
                    f"{self.api_url}/v1/scrape",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"url": request.url, "formats": ["markdown", "html"]},
                )
                if resp.status_code != 200:
                    return ScrapeResult(
                        url=request.url,
                        final_url=request.url,
                        status_code=resp.status_code,
                        scrape_mode_used=ScrapeMode.FIRECRAWL,
                        metadata={"error": f"Firecrawl HTTP status {resp.status_code}"},
                    )

                data = resp.json().get("data", {})
                content = data.get("markdown") or data.get("html", "")
                metadata = data.get("metadata", {})

                return ScrapeResult(
                    url=request.url,
                    final_url=metadata.get("sourceURL", request.url),
                    status_code=200,
                    markdown=content,
                    title=metadata.get("title", ""),
                    scrape_mode_used=ScrapeMode.FIRECRAWL,
                    metadata=metadata,
                )
        except Exception as exc:
            log.error("Firecrawl scraping failed", url=request.url, error=str(exc))
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.FIRECRAWL,
                metadata={"error": str(exc)},
            )

    async def scrape_many(
        self, requests: list[ScrapeRequest], *, max_concurrency: int = 5
    ) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently via Firecrawl."""
        import asyncio

        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        tasks = [_bounded(req) for req in requests]
        return list(await asyncio.gather(*tasks))

    async def health_check(self) -> bool:
        """Return True if Firecrawl API is configured and operational."""
        return self.api_key is not None

