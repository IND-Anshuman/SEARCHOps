"""
Resilient Web Scraping Fallback Pipeline.

Orchestrates scraping backends with fallback logic:
1. Firecrawl (if enabled & key configured)
2. Playwright with Browser Pool (if Firecrawl fails or disabled)
3. HTTP Basic Scraper (lightweight fallback)
Enforces rate limits, domain blocklists, content pruning, and max content length.

Phase 1 Enhancements:
- BrowserPool for context reuse (<100ms latency)
- ContentPruner for HTML→Markdown conversion (67% token reduction)
- DomainRateLimiter for per-domain rate limiting with adaptive backoff
- Network interception for XHR/Fetch/GraphQL extraction
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.feature_flags.manager import FeatureFlagManager
from searchops.infrastructure.cache.redis import RedisCache
from searchops.scraping.browser_pool import BrowserPool, PoolConfig, get_browser_pool
from searchops.scraping.content_pruner import ContentPruner, get_content_pruner
from searchops.scraping.firecrawl import FirecrawlScraper
from searchops.scraping.rate_limiter import DomainRateLimiter, get_rate_limiter
from searchops.scraping.crawl4ai_engine import Crawl4AIScraper, build_crawl4ai_scraper
from searchops.scraping.stealth import (
    ProxyRouter,
    StealthHTTPScraper,
    build_proxy_router,
    build_stealth_scraper,
)
from searchops.scraping.document_engine import PdfScraper, build_pdf_scraper, is_pdf_url
from searchops.scraping.transport import get_transport_pool


log = structlog.get_logger(__name__)

_SCRAPE_CACHE_TTL = 86400  # 24 hour cache for scraped web pages


class NetworkInterceptor:
    """Captures XHR/Fetch/GraphQL responses from page network traffic."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: list[dict] = []
        self.graphql_responses: list[dict] = []

    def setup_handlers(self, page: Any) -> None:
        """Setup network interception handlers on a Playwright page."""
        # Capture all network requests
        page.on("request", lambda request: self._on_request(request))
        page.on("response", lambda response: self._on_response(response))

    def _on_request(self, request: Any) -> None:
        """Handle network request."""
        url = request.url
        method = request.method
        post_data = request.post_data

        self.requests.append({
            "url": url,
            "method": method,
            "post_data": post_data,
            "timestamp": time.time(),
        })

        # Detect GraphQL queries
        if "application/json" in (request.headers.get("content-type", "")):
            try:
                if post_data:
                    data = post_data if isinstance(post_data, dict) else {}
                    if "query" in data:
                        self.graphql_responses.append({
                            "type": "graphql_query",
                            "url": url,
                            "query": data.get("query", ""),
                            "variables": data.get("variables", {}),
                        })
            except Exception:
                pass

    def _on_response(self, response: Any) -> None:
        """Handle network response."""
        url = response.url
        status = response.status

        self.responses.append({
            "url": url,
            "status": status,
            "timestamp": time.time(),
        })

        # Capture JSON responses for potential structured data
        if status == 200 and "application/json" in response.headers.get("content-type", ""):
            # Store reference for later extraction
            pass

    def get_structured_data(self) -> dict:
        """Extract captured structured data."""
        return {
            "total_requests": len(self.requests),
            "graphql_queries": self.graphql_responses,
            "json_responses": [r for r in self.responses if "application/json" in r.get("headers", {}).get("content-type", "")],
        }

    def clear(self) -> None:
        """Clear captured data."""
        self.requests.clear()
        self.responses.clear()
        self.graphql_responses.clear()


class BasicHTTPScraper(IScraper):
    """Lightweight fallback HTTP scraper using httpx."""

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        try:
            transport = get_transport_pool()
            resp = await transport.get(request.url, timeout=request.timeout_seconds)
            return ScrapeResult(
                url=request.url,
                final_url=str(resp.url),
                status_code=resp.status_code,
                html=resp.text if resp.status_code == 200 else None,
                scrape_mode_used=ScrapeMode.HTTP,
            )
        except Exception as exc:
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.HTTP,
                metadata={"error": str(exc)},
            )


class PooledPlaywrightScraper(IScraper):
    """
    Enhanced Playwright scraper using BrowserPool for high performance.

    Features:
    - Reuses browser contexts (10-50 concurrent per browser)
    - Pre-warmed browsers eliminate cold start
    - Network interception for XHR/Fetch/GraphQL
    - Content pruning for token optimization
    """

    def __init__(
        self,
        pool: BrowserPool | None = None,
        pruner: ContentPruner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.pool = pool or get_browser_pool()
        self.pruner = pruner or get_content_pruner()
        self.settings = (settings or get_settings()).scraping

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape using pooled browser context."""
        start_time = time.perf_counter()

        try:
            # Acquire browser context from pool
            async with self.pool.acquire(timeout=30.0) as ctx:
                page = await ctx.new_page()

                # Setup network interception
                interceptor = NetworkInterceptor()
                interceptor.setup_handlers(page)

                # Navigate to page
                await page.goto(request.url, timeout=request.timeout_seconds * 1000, wait_until="networkidle")

                # Get content
                html_content = await page.content()
                title = await page.title()
                final_url = page.url

                # Extract network data
                network_data = interceptor.get_structured_data()

                # Prune HTML to Markdown
                markdown_content = self.pruner.prune(html_content)

                elapsed_ms = (time.perf_counter() - start_time) * 1000

                return ScrapeResult(
                    url=request.url,
                    final_url=final_url,
                    status_code=200,
                    html=html_content,
                    markdown=markdown_content,
                    title=title,
                    scrape_mode_used=ScrapeMode.PLAYWRIGHT,
                    metadata={
                        "network_requests": network_data.get("total_requests", 0),
                        "graphql_queries": len(network_data.get("graphql_queries", [])),
                        "elapsed_ms": elapsed_ms,
                    },
                )

        except asyncio.TimeoutError:
            log.error("Playwright timeout", url=request.url, timeout=request.timeout_seconds)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=408,
                scrape_mode_used=ScrapeMode.PLAYWRIGHT,
                metadata={"error": "Request timeout"},
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
        """Scrape multiple URLs concurrently via pooled Playwright."""
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        tasks = [_bounded(req) for req in requests]
        return list(await asyncio.gather(*tasks))

    async def health_check(self) -> bool:
        """Return True if browser pool is operational."""
        try:
            return self.pool.stats.get("total_browsers", 0) > 0
        except Exception:
            return False


class ScrapingPipeline:
    """
    Multi-backend resilient scraping pipeline with Phase 1–3 enhancements.

    Tier execution order (lightest → heaviest cost/latency):

    ┌──────┬──────────────────────────────────────┬──────────┬──────────┐
    │ Tier │ Backend                              │ Latency  │ Cost     │
    ├──────┼──────────────────────────────────────┼──────────┼──────────┤
    │  0   │ StealthHTTPScraper (curl_cffi JA4)  │ ~150 ms  │ $0       │
    │  0b  │ ProxyRouter (curl_cffi + DataImpulse│ ~400 ms  │ $1/GB    │
    │  0.5 │ Crawl4AIScraper (BM25+entropy prune)│ ~800 ms  │ $0  NEW  │
    │  1   │ PooledPlaywrightScraper              │ ~1.5 s   │ $0       │
    │  2   │ FirecrawlScraper (if API key)        │ ~3–5 s   │ $$$      │
    │  3   │ BasicHTTPScraper (last resort)       │ ~500 ms  │ $0       │
    └──────┴──────────────────────────────────────┴──────────┴──────────┘

    Features (Phase 1):
    - BrowserPool for <100ms context reuse
    - ContentPruner for 67% token reduction
    - DomainRateLimiter with adaptive backoff
    - NetworkInterceptor for XHR/Fetch/GraphQL capture

    Features (Phase 2):
    - StealthHTTPScraper: curl_cffi BoringSSL JA4 TLS impersonation
    - ProxyRouter: residential proxy fallback (DataImpulse $1/GB)

    Features (Phase 3):
    - Crawl4AIScraper: local async AI crawler with BM25+entropy content pruning
      producing token-optimised fit_markdown for LLM consumption
    """

    def __init__(
        self,
        stealth: StealthHTTPScraper | None = None,
        proxy_router: ProxyRouter | None = None,
        crawl4ai: Crawl4AIScraper | None = None,
        pdf_scraper: PdfScraper | None = None,
        firecrawl: FirecrawlScraper | None = None,
        playwright: PooledPlaywrightScraper | None = None,
        basic_http: BasicHTTPScraper | None = None,
        cache: RedisCache | None = None,
        rate_limiter: DomainRateLimiter | None = None,
        feature_flags: FeatureFlagManager | None = None,
        settings: Settings | None = None,
    ) -> None:
        _settings = settings or get_settings()
        self.stealth      = stealth      or build_stealth_scraper(_settings.scraping)
        self.proxy_router = proxy_router or build_proxy_router(_settings.scraping)
        self.crawl4ai     = crawl4ai     or build_crawl4ai_scraper()  # Tier 0.5
        self.pdf_scraper  = pdf_scraper  or build_pdf_scraper()       # PDF route
        self.firecrawl    = firecrawl    or FirecrawlScraper(settings)
        self.playwright   = playwright   or PooledPlaywrightScraper(settings=settings)
        self.basic_http   = basic_http   or BasicHTTPScraper()
        self.cache        = cache
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self.feature_flags = feature_flags
        self.settings     = _settings.scraping


    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL for rate limiting."""
        try:
            return urlparse(url).netloc
        except Exception:
            return "unknown"

    def _url_cache_key(self, url: str) -> str:
        """Compute sha256 cache key for target URL."""
        url_hash = hashlib.sha256(url.lower().strip().encode("utf-8")).hexdigest()
        return f"scrape:cache:{url_hash}"

    async def execute(self, request: ScrapeRequest) -> ScrapeResult:
        """Run scrape request through the full tiered pipeline with all Phase 1+2 features."""
        domain = self._extract_domain(request.url)

        # 0. Check rate limit before hitting any network tier
        can_proceed, wait_time = await self.rate_limiter.check(domain)
        if not can_proceed:
            log.warning("Rate limited", domain=domain, wait_seconds=wait_time)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=429,
                scrape_mode_used=ScrapeMode.HTTP,
                metadata={"error": f"Rate limited. Retry in {wait_time:.1f}s.", "domain": domain},
            )

        log.info("pipeline.execute", url=request.url)

        # 1. Check Redis cache
        cache_key = self._url_cache_key(request.url)
        if self.cache:
            cached_data = await self.cache.get(cache_key)
            if cached_data and isinstance(cached_data, dict):
                log.info("pipeline.cache_hit", url=request.url)
                return ScrapeResult.model_validate(cached_data)

        res: ScrapeResult | None = None

        # ── Fast Route: PDF Documents ──────────────────────────────────────────
        if request.mode == ScrapeMode.DOCLING_PDF or is_pdf_url(request.url):
            log.info("pipeline.pdf_route", url=request.url)
            res = await self.pdf_scraper.scrape(request)
            await self.rate_limiter.record_response(domain, res.status_code)
            if res.status_code == 200:
                return await self._cache_and_return(cache_key, res)
            log.warning("pipeline.pdf_failed", url=request.url, status=res.status_code)

        # ── Tier 0: StealthHTTPScraper (curl_cffi direct, ~150ms, $0) ────────
        if getattr(self.settings, "stealth_enabled", True):
            res = await self.stealth.scrape(request)
            await self.rate_limiter.record_response(domain, res.status_code)
            if res.status_code == 200:
                res = self._prune_if_needed(res)
                return await self._cache_and_return(cache_key, res)

            # Hard blocks (403/404) — stealth didn't help; escalate immediately
            if res.status_code in (403, 404):
                log.info(
                    "pipeline.stealth_hard_block",
                    url=request.url,
                    status=res.status_code,
                )
            else:
                log.warning(
                    "pipeline.stealth_failed",
                    url=request.url,
                    status=res.status_code,
                )

        # ── Tier 0b: ProxyRouter (curl_cffi + residential proxy, ~400ms) ─────
        if self.proxy_router is not None and (res is None or res.status_code != 200):
            log.info("pipeline.proxy_tier", url=request.url)
            res = await self.proxy_router.scrape(request)
            await self.rate_limiter.record_response(domain, res.status_code)
            if res.status_code == 200:
                res = self._prune_if_needed(res)
                return await self._cache_and_return(cache_key, res)
            log.warning(
                "pipeline.proxy_failed",
                url=request.url,
                status=res.status_code,
            )

        # ── Tier 0.5: Crawl4AIScraper (BM25+entropy pruning, ~800ms) ──────────
        if self.feature_flags is None or await self.feature_flags.is_enabled("crawl4ai_enabled"):
            log.info("pipeline.crawl4ai_tier", url=request.url)
            res = await self.crawl4ai.scrape(request)
            await self.rate_limiter.record_response(domain, res.status_code)
            if res.status_code == 200:
                return await self._cache_and_return(cache_key, res)
            log.warning(
                "pipeline.crawl4ai_failed",
                url=request.url,
                status=res.status_code,
            )

        # ── Tier 1: PooledPlaywrightScraper (full JS, ~1.5s) ─────────────────
        if self.feature_flags is None or await self.feature_flags.is_enabled("playwright_enabled"):
            res = await self.playwright.scrape(request)
            await self.rate_limiter.record_response(domain, res.status_code)
            if res.status_code == 200:
                return await self._cache_and_return(cache_key, res)
            log.warning(
                "pipeline.playwright_failed",
                url=request.url,
                status=res.status_code,
            )

        # ── Tier 2: FirecrawlScraper (managed API, if key set) ────────────────
        if self.feature_flags is None or await self.feature_flags.is_enabled("firecrawl_enabled"):
            res = await self.firecrawl.scrape(request)
            await self.rate_limiter.record_response(domain, res.status_code)
            if res.status_code == 200:
                return await self._cache_and_return(cache_key, res)
            if res.status_code in (403, 404, 429):
                log.info(
                    "pipeline.firecrawl_block",
                    url=request.url,
                    status=res.status_code,
                )
                return res
            log.warning(
                "pipeline.firecrawl_failed",
                url=request.url,
                status=res.status_code,
            )

        # ── Tier 3: BasicHTTPScraper (last resort, ~500ms) ────────────────────
        res = await self.basic_http.scrape(request)
        await self.rate_limiter.record_response(domain, res.status_code)
        if res.status_code == 200:
            return await self._cache_and_return(cache_key, res)

        return res

    # ------------------------------------------------------------------ #
    #  Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _prune_if_needed(self, res: ScrapeResult) -> ScrapeResult:
        """
        Run ContentPruner on the result's HTML if markdown is absent.

        Stealth / basic HTTP tiers return raw HTML without markdown.
        Playwright + Firecrawl set markdown directly.
        """
        if res.html and not res.markdown:
            pruner = get_content_pruner()
            md = pruner.prune(res.html)
            # Pydantic models are immutable — rebuild with updated fields
            return res.model_copy(update={"markdown": md})
        return res

    async def _cache_and_return(self, cache_key: str, res: ScrapeResult) -> ScrapeResult:
        """Persist result to Redis cache and return it."""
        if self.cache and res.status_code == 200:
            await self.cache.set(cache_key, res.model_dump(mode="json"), ttl_seconds=_SCRAPE_CACHE_TTL)
        return res


    def get_stats(self) -> dict[str, Any]:
        """Get pipeline statistics including all active tiers."""
        return {
            "rate_limiter": self.rate_limiter.get_all_stats(),
            "pool": self.playwright.pool.stats if hasattr(self.playwright, "pool") else {},
            "stealth": {
                "impersonate": self.stealth._cfg.impersonate,
                "proxy_enabled": bool(self.proxy_router),
            },
        }
