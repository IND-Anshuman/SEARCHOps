"""Page text from the Tavily Extract API — the fetcher for ``TOOL_PROFILE=api``.

The free stack (``pipeline.ScrapingPipeline``) fetches pages itself through a
ladder of local tools: stealth HTTP, Crawl4AI, a headless browser, PDF parsers.
That needs Chromium and several hundred MB of packages. In the API profile one
HTTPS call to Tavily returns the page's main content as Markdown instead, PDFs
included, with no browser anywhere.

It exposes the one method the scrape node uses, ``execute``, so the graph does
not know which fetcher it was given.

Cost: Tavily bills 1 credit per 5 successful extractions ("basic") and nothing
for failures, cumulatively, so one call per URL costs the same as batching.
Extracted pages are cached in Redis for a day, so a resumed or repeated run
does not pay twice.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult

if TYPE_CHECKING:
    from searchops.infrastructure.cache.redis import RedisCache

log = structlog.get_logger(__name__)

EXTRACT_URL = "https://api.tavily.com/extract"
_CACHE_TTL_SECONDS = 24 * 3600
# Tavily's own per-URL limit is 10 s (basic) / 30 s (advanced); leave headroom.
_HTTP_TIMEOUT = {"basic": 25.0, "advanced": 45.0}


class TavilyExtractPipeline:
    """Fetches one page per ``execute`` call through Tavily Extract."""

    def __init__(self, settings: Settings | None = None, cache: RedisCache | None = None) -> None:
        cfg = settings or get_settings()
        key = cfg.search.tavily_api_key
        self.api_key = key.get_secret_value() if key else ""
        self.depth = cfg.scraping.tavily_extract_depth
        self.cache = cache
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT[self.depth])
        return self._client

    async def execute(self, request: ScrapeRequest, job_id: str = "", agent_id: str | None = None) -> ScrapeResult:
        url = request.url
        started = time.perf_counter()
        if not self.api_key:
            log.warning("tavily_extract.no_api_key", url=url)
            return _failed(url, 401, "TAVILY_API_KEY is not set", started)

        cache_key = f"tavily:extract:{self.depth}:{hashlib.sha256(url.encode()).hexdigest()}"
        if self.cache is not None:
            hit = await self.cache.get(cache_key)
            if isinstance(hit, dict) and hit.get("markdown"):
                return _result(url, hit["markdown"], started, cached=True)

        try:
            resp = await self._http().post(
                EXTRACT_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"urls": [url], "extract_depth": self.depth, "format": "markdown"},
            )
        except httpx.HTTPError as exc:
            log.warning("tavily_extract.request_failed", url=url, job_id=job_id, error=str(exc))
            return _failed(url, 599, f"request failed: {exc}", started)

        if resp.status_code != 200:
            # 401 bad key, 429 rate limit, 432/433 plan or credit limits.
            log.warning("tavily_extract.http_error", url=url, status=resp.status_code, body=resp.text[:200])
            return _failed(url, resp.status_code, resp.text[:200], started)

        body: dict[str, Any] = resp.json()
        page = next((r for r in body.get("results") or [] if r.get("raw_content")), None)
        if page is None:
            reason = next((f.get("error", "") for f in body.get("failed_results") or []), "no content")
            log.info("tavily_extract.no_content", url=url, reason=str(reason)[:160])
            return _failed(url, 422, str(reason), started)

        markdown = str(page["raw_content"])
        if self.cache is not None:
            await self.cache.set(cache_key, {"markdown": markdown}, ttl_seconds=_CACHE_TTL_SECONDS)
        return _result(url, markdown, started, cached=False)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _result(url: str, markdown: str, started: float, *, cached: bool) -> ScrapeResult:
    return ScrapeResult(
        url=url,
        final_url=url,
        status_code=200,
        markdown=markdown,
        content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
        word_count=len(markdown.split()),
        was_cached=cached,
        scrape_mode_used=ScrapeMode.TAVILY_EXTRACT,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )


def _failed(url: str, status: int, reason: str, started: float) -> ScrapeResult:
    return ScrapeResult(
        url=url,
        final_url=url,
        status_code=status,
        scrape_mode_used=ScrapeMode.TAVILY_EXTRACT,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        metadata={"error": reason},
    )
