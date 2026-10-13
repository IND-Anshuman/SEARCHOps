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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from searchops.config.settings import Settings, get_settings
from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.brightdata_browser import BrightDataBrowserScraper, build_bd_browser
from searchops.scraping.brightdata_unlocker import BrightDataUnlockerScraper, build_bd_unlocker

if TYPE_CHECKING:
    from searchops.scraping.bd_spend_guard import BrightDataSpendGuard
from searchops.scraping.browser_pool import BrowserPool, get_browser_pool
from searchops.scraping.content_pruner import ContentPruner, get_content_pruner
from searchops.scraping.crawl4ai_engine import Crawl4AIScraper, build_crawl4ai_scraper
from searchops.scraping.deduplicator import PersistentMinHashDeduplicator
from searchops.scraping.document_engine import PdfScraper, build_pdf_scraper, is_pdf_url
from searchops.scraping.firecrawl import FirecrawlScraper
from searchops.scraping.rate_limiter import DomainRateLimiter, get_rate_limiter
from searchops.scraping.robots import RobotsGateStats, robots_crawl_delay, robots_url_allowed
from searchops.scraping.stealth import (
    ProxyRouter,
    StealthHTTPScraper,
    build_proxy_router,
    build_stealth_scraper,
)
from searchops.scraping.tier_bandit import TierBandit
from searchops.scraping.transport import get_transport_pool
from searchops.scraping.wall_detector import WallClass, detect_wall

if TYPE_CHECKING:
    from searchops.feature_flags.manager import FeatureFlagManager
    from searchops.infrastructure.cache.redis import RedisCache
    from searchops.scraping.url_seen import URLSeenSet

log = structlog.get_logger(__name__)

_SCRAPE_CACHE_TTL = 86400  # 24 hour cache for scraped web pages

# Documents shorter than this are not worth fingerprinting: 3-word shingles on a
# few hundred characters produce so few signatures that near-dup detection is
# unreliable, and the LLM call downstream dominates the cost anyway.
_MIN_DEDUP_CHARS = 500


class NetworkInterceptor:
    """Captures XHR/Fetch/GraphQL responses from page network traffic."""

    # Cap on captured JSON bodies. A single page can issue hundreds of JSON
    # requests; storing every body would let one scrape balloon memory.
    _MAX_JSON_BODIES = 20

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self.graphql_responses: list[dict[str, Any]] = []
        self.json_responses: list[dict[str, Any]] = []

    def setup_handlers(self, page: Any) -> None:
        """Setup network interception handlers on a Playwright page."""
        # Both handlers are coroutine functions: the response handler has to
        # await response.json(), and Playwright accepts awaitable listeners.
        page.on("request", lambda request: asyncio.ensure_future(self._on_request(request)))
        page.on("response", lambda response: asyncio.ensure_future(self._on_response(response)))

    async def _on_request(self, request: Any) -> None:
        """Handle network request."""
        url = request.url
        method = request.method
        post_data = request.post_data

        self.requests.append(
            {
                "url": url,
                "method": method,
                "post_data": post_data,
                "timestamp": time.time(),
            }
        )

        # Detect GraphQL queries
        if "application/json" in (request.headers.get("content-type", "")):
            try:
                if post_data:
                    data = post_data if isinstance(post_data, dict) else {}
                    if "query" in data:
                        self.graphql_responses.append(
                            {
                                "type": "graphql_query",
                                "url": url,
                                "query": data.get("query", ""),
                                "variables": data.get("variables", {}),
                            }
                        )
            except Exception:
                pass

    async def _on_response(self, response: Any) -> None:
        """Handle network response."""
        url = response.url
        status = response.status

        self.responses.append(
            {
                "url": url,
                "status": status,
                "timestamp": time.time(),
            }
        )

        # Capture JSON bodies for potential structured data. Only successful
        # responses, and only up to the cap.
        if (
            status == 200
            and "application/json" in response.headers.get("content-type", "")
            and len(self.json_responses) < self._MAX_JSON_BODIES
        ):
            try:
                body = await response.json()
                self.json_responses.append(
                    {
                        "url": url,
                        "status": status,
                        "body": body,
                        "timestamp": time.time(),
                    }
                )
            except Exception:
                # Not every JSON-labelled response carries a parseable body.
                pass

    def get_structured_data(self) -> dict[str, Any]:
        """Extract captured structured data."""
        return {
            "total_requests": len(self.requests),
            "graphql_queries": self.graphql_responses,
            "json_responses": self.json_responses,
        }

    def clear(self) -> None:
        """Clear captured data."""
        self.requests.clear()
        self.responses.clear()
        self.graphql_responses.clear()
        self.json_responses.clear()


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

    async def scrape_many(self, requests: list[ScrapeRequest], *, max_concurrency: int = 5) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently via the shared transport pool."""
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*(_bounded(req) for req in requests)))

    async def health_check(self) -> bool:
        """Return True if the shared HTTP transport pool is usable."""
        try:
            return get_transport_pool() is not None
        except Exception:
            return False


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

        except TimeoutError:
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

    async def scrape_many(self, requests: list[ScrapeRequest], *, max_concurrency: int = 5) -> list[ScrapeResult]:
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
            return bool(self.pool.stats.get("total_browsers", 0) > 0)
        except Exception:
            return False


@dataclass
class WallGateStats:
    """Counters for the bot-wall gate on HTTP 200 responses."""

    inspected: int = 0
    rejected: int = 0
    by_class: dict[str, int] = field(default_factory=lambda: {w.value: 0 for w in WallClass})

    def histogram(self) -> dict[str, int]:
        return dict(self.by_class)


class ScrapingPipeline:
    """
    Multi-backend resilient scraping pipeline with Free + Premium tiers.

    Tier execution order (lightest → heaviest cost/latency):

    ┌──────┬──────────────────────────────────────┬──────────┬──────────────┬────────────┐
    │ Prio │ Backend                              │ Latency  │ Cost         │ Class      │
    ├──────┼──────────────────────────────────────┼──────────┼──────────────┼────────────┤
    │  0   │ StealthHTTPScraper (curl_cffi JA4)   │ ~150 ms  │ $0           │ FREE       │
    │  1   │ ProxyRouter (DataImpulse/Decodo)     │ ~400 ms  │ $1-2/GB      │ FREE       │
    │  2   │ Crawl4AIScraper (BM25+entropy prune) │ ~800 ms  │ $0           │ FREE       │
    │  3   │ PooledPlaywrightScraper              │ ~1.5 s   │ $0           │ FREE       │
    │  4   │ BrightDataUnlockerScraper ★ PREMIUM  │ ~1-2 s   │ BD credits   │ PREMIUM    │
    │  5   │ FirecrawlScraper                     │ ~3-5 s   │ $$$          │ FREE(paid) │
    │  6   │ BrightDataBrowserScraper ★ PREMIUM   │ ~3-6 s   │ BD credits   │ PREMIUM    │
    │  7   │ BasicHTTPScraper (last resort)       │ ~500 ms  │ $0           │ FREE       │
    └──────┴──────────────────────────────────────┴──────────┴──────────────┴────────────┘

    Premium tiers activate only when:
      1. Bright Data credentials are configured (BRIGHTDATA_CUSTOMER_ID + BRIGHTDATA_ZONE_PASSWORD)
      2. Feature flag is enabled (brightdata_unlocker_enabled / brightdata_browser_enabled)
      3. Previous failure is ACCESS class (403/429/503) — NOT on 404 content failures
      4. BD spend guard approves the cost (per-job/hour/day limits)
      5. BD circuit breaker is CLOSED or HALF_OPEN
    """

    def __init__(
        self,
        stealth: StealthHTTPScraper | None = None,
        proxy_router: ProxyRouter | None = None,
        crawl4ai: Crawl4AIScraper | None = None,
        pdf_scraper: PdfScraper | None = None,
        firecrawl: FirecrawlScraper | None = None,
        playwright: PooledPlaywrightScraper | None = None,
        bd_unlocker: BrightDataUnlockerScraper | None = None,
        bd_browser: BrightDataBrowserScraper | None = None,
        basic_http: BasicHTTPScraper | None = None,
        cache: RedisCache | None = None,
        rate_limiter: DomainRateLimiter | None = None,
        feature_flags: FeatureFlagManager | None = None,
        spend_guard: BrightDataSpendGuard | None = None,
        settings: Settings | None = None,
        url_seen: URLSeenSet | None = None,
        tier_bandit: TierBandit | None = None,
        deduplicator: Any = None,
        robots_cache: Any = None,
    ) -> None:
        from searchops.scraping.tier import TIER_DEFINITIONS, ScrapeTier
        from searchops.scraping.tier import classify_failure as _clf

        _settings = settings or get_settings()
        self.stealth = stealth or build_stealth_scraper(_settings.scraping)
        self.proxy_router = proxy_router or build_proxy_router(_settings.scraping)
        self.crawl4ai = crawl4ai or build_crawl4ai_scraper()  # Tier 2
        self.pdf_scraper = pdf_scraper or build_pdf_scraper()
        self.firecrawl = firecrawl or FirecrawlScraper(settings)
        self.playwright = playwright or PooledPlaywrightScraper(settings=settings)
        # Premium tiers — None if BD credentials not present
        self.bd_unlocker = bd_unlocker if bd_unlocker is not None else build_bd_unlocker(_settings.scraping)
        self.bd_browser = bd_browser if bd_browser is not None else build_bd_browser(_settings.scraping)
        self.basic_http = basic_http or BasicHTTPScraper()
        self.cache = cache
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self.feature_flags = feature_flags
        self.spend_guard = spend_guard
        self.url_seen = url_seen
        # robots.txt compliance. None (the default) means the gate is
        # unavailable and fetches proceed unmoderated — an operator who wires
        # robots explicitly opts into enforcement.
        self.robots_cache = robots_cache
        self.robots_stats = RobotsGateStats()
        self.wall_stats = WallGateStats()
        # Near-duplicate detection. Defaults to the best backend the current
        # environment supports (persistent when Redis is configured, in-memory
        # otherwise) — dedup is never a hard dependency on infrastructure.
        self.deduplicator = deduplicator if deduplicator is not None else self._build_default_deduplicator(_settings)
        self.settings = _settings.scraping
        self._classify_failure = _clf

        # Build declarative tier list — scrapers injected into ScrapeTier dataclasses
        self._tiers: list[ScrapeTier] = self._build_tiers(TIER_DEFINITIONS)

        # Contextual bandit for learned per-domain tier ordering (Phase 4).
        # If not injected, use the static tier sort (backward compatible).
        self.tier_bandit = tier_bandit or TierBandit(self._tiers)

    def _build_tiers(self, definitions: list[Any]) -> list[Any]:
        """Inject scraper instances into tier definitions and sort by priority."""
        import copy

        scraper_map = {
            "stealth_http": self.stealth,
            "proxy_router": self.proxy_router,
            "crawl4ai": self.crawl4ai,
            "playwright": self.playwright,
            "bd_unlocker": self.bd_unlocker,
            "firecrawl": self.firecrawl,
            "bd_browser": self.bd_browser,
            "basic_http": self.basic_http,
        }
        tiers = []
        for defn in definitions:
            t = copy.copy(defn)
            t.scraper = scraper_map.get(defn.name)
            tiers.append(t)
        return sorted(tiers, key=lambda t: t.priority)

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL for rate limiting."""
        try:
            return urlparse(url).netloc
        except Exception:
            return "unknown"

    async def _apply_crawl_delay(self, domain: str, delay_seconds: float) -> None:
        """Make the site's declared Crawl-Delay its effective rate limit.

        Reuses the existing DomainRateLimiter rather than adding a second
        timing path: the tightest (largest) delay wins, so a permissive default
        config can never override a site's stated requirement.
        """
        try:
            from searchops.scraping.rate_limiter import DomainConfig

            current = self.rate_limiter._get_domain_config(domain)
            # A delay of D seconds allows 60/D requests per minute, floored at 1.
            per_window = max(1, int(60.0 / delay_seconds))
            merged = DomainConfig(
                requests_per_window=per_window,
                window_seconds=60.0,
                backoff_base_seconds=current.backoff_base_seconds,
                backoff_max_seconds=current.backoff_max_seconds,
                backoff_multiplier=current.backoff_multiplier,
                backoff_jitter=current.backoff_jitter,
                recovery_timeout_seconds=current.recovery_timeout_seconds,
            )
            self.rate_limiter.set_domain_config(domain, merged)
            self.robots_stats.delays_applied += 1
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("pipeline.crawl_delay_failed", domain=domain, error=str(exc))

    @staticmethod
    def _build_default_deduplicator(settings: Any) -> Any:
        """Build the near-dedup engine the current environment supports.

        Uses the persistent (Redis-backed) engine when a cache URL is
        configured, so the index survives restarts; otherwise in-memory. Wrapped
        in a broad guard because dedup must never be the reason a scrape fails.
        """
        try:
            from searchops.scraping.deduplicator import build_deduplicator_from_config

            redis_url: str | None = None
            cache_cfg = getattr(getattr(settings, "cache", None), "url", None)
            if cache_cfg is not None:
                # SecretStr or plain str depending on the config surface.
                redis_url = cache_cfg.get_secret_value() if hasattr(cache_cfg, "get_secret_value") else str(cache_cfg)
            return build_deduplicator_from_config(
                threshold=getattr(getattr(settings, "scraping", None), "dedup_threshold", 0.85),
                redis_url=redis_url,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("pipeline.deduplicator_build_failed", error=str(exc))
            from searchops.scraping.deduplicator import MinHashDeduplicator

            return MinHashDeduplicator(threshold=0.85)

    def _url_cache_key(self, url: str) -> str:
        """Compute sha256 cache key for target URL."""
        url_hash = hashlib.sha256(url.lower().strip().encode("utf-8")).hexdigest()
        return f"scrape:cache:{url_hash}"

    async def execute(self, request: ScrapeRequest, job_id: str = "", agent_id: str | None = None) -> ScrapeResult:
        """Run scrape request through the full tiered pipeline with all security/reliability features."""
        from searchops.feature_flags.manager import FeatureFlagSnapshot
        from searchops.scraping.bd_circuit_breaker import get_bd_breaker
        from searchops.scraping.bd_spend_guard import SpendDecision
        from searchops.scraping.tier import FailureClass, classify_failure

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

        # 0a. robots.txt compliance. A disallowed path is skipped outright —
        # no tier runs — and recorded as an intentional skip, not a failure.
        if self.robots_cache is not None:
            rules = await self.robots_cache.get(request.url)
            if not robots_url_allowed(request.url, rules):
                log.info("pipeline.robots_skip", url=request.url, domain=domain)
                self.robots_stats.skipped += 1
                delay = robots_crawl_delay(rules)
                if delay:
                    await self._apply_crawl_delay(domain, delay)
                return ScrapeResult(
                    url=request.url,
                    final_url=request.url,
                    status_code=403,
                    scrape_mode_used=ScrapeMode.HTTP,
                    metadata={
                        "skipped": "robots",
                        "reason": "Disallowed by robots.txt",
                        "domain": domain,
                    },
                )
            # Honour the site's declared politeness as its rate limit.
            delay = robots_crawl_delay(rules)
            if delay:
                await self._apply_crawl_delay(domain, delay)

        # 0b. Frontier de-duplication — skip URLs this crawl has already fetched.
        # The content cache below only helps when a *successful* result was
        # stored; the seen-set also suppresses re-fetching URLs that failed or
        # were never cached, so the crawl does not pay for them repeatedly.
        if self.url_seen is not None:
            already_seen = await self.url_seen.seen(request.url)
            if already_seen:
                log.info("pipeline.seen_skip", url=request.url, domain=domain)
                return ScrapeResult(
                    url=request.url,
                    final_url=request.url,
                    status_code=208,  # Already Reported
                    scrape_mode_used=ScrapeMode.HTTP,
                    metadata={
                        "skipped": "already_seen",
                        "domain": domain,
                        "seen_skip_rate": round(self.url_seen.stats.skip_rate, 4),
                    },
                )

        # 1. Check Redis cache
        cache_key = self._url_cache_key(request.url)
        if self.cache:
            cached_data = await self.cache.get(cache_key)
            if cached_data and isinstance(cached_data, dict):
                log.info("pipeline.cache_hit", url=request.url)
                return ScrapeResult.model_validate(cached_data)

        # ── Fast Route: PDF Documents ──────────────────────────────────────────
        if request.mode == ScrapeMode.DOCLING_PDF or is_pdf_url(request.url):
            log.info("pipeline.pdf_route", url=request.url)
            pdf_res = await self.pdf_scraper.scrape(request)
            await self.rate_limiter.record_response(domain, pdf_res.status_code)
            if pdf_res.status_code == 200:
                return await self._cache_and_return(cache_key, pdf_res)
            log.warning("pipeline.pdf_failed", url=request.url, status=pdf_res.status_code)

        # 2. Snapshot ALL feature flags ONCE — eliminates 5x async lookups in hot path
        if self.feature_flags is not None:
            flags = await self.feature_flags.snapshot()
        else:
            flags = FeatureFlagSnapshot.empty()

        # 3. Declarative tier loop — iterate sorted ScrapeTier list
        res: ScrapeResult | None = None
        last_status: int | None = None
        last_failure_class: FailureClass | None = None
        flag_dict = flags._values  # Raw dict access for is_eligible()

        for tier in self.tier_bandit.ordered_tiers(domain):
            # Check tier eligibility (feature flag + trigger_on constraint)
            if not tier.is_eligible(last_failure_class, flag_dict):
                continue

            # Check circuit breaker for premium tiers
            if tier.is_premium:
                breaker = get_bd_breaker(tier.name)
                if not breaker.can_execute():
                    log.warning(
                        "pipeline.circuit_open: skipping premium tier",
                        tier=tier.name,
                        url=request.url,
                    )
                    from searchops.scraping.bd_metrics import BD_REQUESTS

                    BD_REQUESTS.labels(tier=tier.name.replace("bd_", ""), outcome="circuit_open").inc()
                    continue

                # Check spend guard for premium tiers
                if self.spend_guard is not None and tier.cost_usd > 0:
                    decision = await self.spend_guard.check_and_reserve(
                        cost_usd=tier.cost_usd,
                        job_id=job_id,
                        tier=tier.name,
                        agent_id=agent_id,
                    )
                    if decision == SpendDecision.HARD_REJECTED:
                        log.warning(
                            "pipeline.budget_exceeded: skipping premium tier",
                            tier=tier.name,
                            url=request.url,
                        )
                        continue

            log.info(
                f"pipeline.{tier.name}_tier",
                url=request.url,
                triggering_status=last_status,
                is_premium=tier.is_premium,
            )

            # Execute the tier
            try:
                res = await tier.scraper.scrape(request)  # type: ignore[union-attr]
            except Exception as exc:
                log.error(f"pipeline.{tier.name}_exception", url=request.url, error=str(exc))
                if tier.is_premium:
                    await get_bd_breaker(tier.name).record_failure()
                    if self.spend_guard and tier.cost_usd > 0:
                        await self.spend_guard.rollback(tier.cost_usd, job_id, tier.name, agent_id)
                res = ScrapeResult(
                    url=request.url,
                    final_url=request.url,
                    status_code=500,
                    scrape_mode_used=ScrapeMode.HTTP,
                    metadata={"error": str(exc)},
                )

            await self.rate_limiter.record_response(domain, res.status_code)

            if res.status_code == 200:
                if tier.is_premium:
                    await get_bd_breaker(tier.name).record_success()
                self.tier_bandit.record_result(domain, tier.name, res.status_code, tier.cost_usd)
                res = self._prune_if_needed(res)
                # A 200 carrying a consent/CAPTCHA/login wall is not content.
                # Reclassify it so the evidence layer never sees it as success.
                res = self._check_for_wall(res, domain)
                if res.status_code != 200:
                    # Escalating tiers would only fetch the same wall again at
                    # higher cost — the page itself is gated, not our request.
                    # Return the classified result so the wall metadata, and
                    # the failure class, survive to the caller.
                    await self.rate_limiter.record_response(domain, res.status_code)
                    return res
                return await self._cache_and_return(cache_key, res)

            # Track failure for next tier's eligibility check
            last_status = res.status_code
            last_failure_class = classify_failure(res.status_code)

            # Record failure in circuit breaker
            if tier.is_premium:
                await get_bd_breaker(tier.name).record_failure()
                if self.spend_guard and tier.cost_usd > 0:
                    await self.spend_guard.rollback(tier.cost_usd, job_id, tier.name, agent_id)

            log.warning(
                f"pipeline.{tier.name}_failed",
                url=request.url,
                status=res.status_code,
                failure_class=last_failure_class,
            )
            self.tier_bandit.record_result(domain, tier.name, res.status_code, tier.cost_usd)

        # All tiers exhausted
        return res or ScrapeResult(
            url=request.url,
            final_url=request.url,
            status_code=500,
            scrape_mode_used=ScrapeMode.HTTP,
            metadata={"error": "All scraping tiers exhausted"},
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _check_for_wall(self, res: ScrapeResult, domain: str) -> ScrapeResult:
        """Reclassify a 200 that is actually a bot wall as a failure.

        A consent banner, CAPTCHA interstitial or login gate all return 200
        with no usable content. Letting those through meant the evidence layer
        accumulated garbage that every later stage then reasoned over.

        Returns the result unchanged for real content.
        """
        if res.status_code != 200:
            return res
        body = res.markdown or res.html or ""
        if not body.strip():
            return res

        result = detect_wall(body)
        self.wall_stats.inspected += 1
        self.wall_stats.by_class[result.wall_class.value] += 1

        if not result.is_wall:
            return res

        self.wall_stats.rejected += 1
        log.info(
            "pipeline.wall_detected",
            url=res.url,
            wall_class=result.wall_class.value,
            marker=result.marker,
            text_length=result.text_length,
        )
        return res.model_copy(
            update={
                "status_code": 403,
                "metadata": {
                    **res.metadata,
                    "error": f"Bot wall detected: {result.wall_class.value}",
                    "wall_class": result.wall_class.value,
                    "wall_marker": result.marker,
                    "domain": domain,
                },
            }
        )

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
        """Persist result to Redis cache and return it.

        Also records the source URL on the frontier and indexes the document's
        content fingerprint, so a retry does not re-fetch a URL already
        completed and a *different* URL carrying near-identical content is
        detected before it is paid for again.
        """
        if self.cache and res.status_code == 200:
            await self.cache.set(cache_key, res.model_dump(mode="json"), ttl_seconds=_SCRAPE_CACHE_TTL)
        if self.url_seen is not None:
            # seen() records on every call, so any successful path through here
            # marks the URL. We call it to persist the frontier entry; the
            # return (new/repeat) is discarded for the success path.
            await self.url_seen.seen(res.final_url or res.url, force=True)

        self._index_content(res)
        return res

    def _index_content(self, res: ScrapeResult) -> None:
        """Record a successful result's content fingerprint for near-dup detection.

        Failures are swallowed: a dedup-index problem must never fail a scrape
        that already succeeded. The metadata is attached to the result so the
        caller can see whether this document duplicated an earlier one.
        """
        if self.deduplicator is None or res.status_code != 200:
            return
        try:
            content = (res.markdown or res.html or "").strip()
            if len(content) < _MIN_DEDUP_CHARS:
                return

            doc_id = res.final_url or res.url
            is_dup, matched = self.deduplicator.is_duplicate(content)
            if is_dup:
                log.info(
                    "pipeline.near_duplicate",
                    url=res.url,
                    matched=matched[:5],
                )
                # Surface it on the result; downstream can decide to discard.
                res.metadata["near_duplicate_of"] = matched[:5]
            self.deduplicator.index_document(doc_id, content)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("pipeline.dedup_index_failed", url=res.url, error=str(exc))

    def get_stats(self) -> dict[str, Any]:
        """Get pipeline statistics including all active tiers."""
        return {
            "rate_limiter": self.rate_limiter.get_all_stats(),
            "pool": self.playwright.pool.stats if hasattr(self.playwright, "pool") else {},
            "stealth": {
                "impersonate": self.stealth._cfg.impersonate,
                "proxy_enabled": bool(self.proxy_router),
            },
            "tier_bandit": self.tier_bandit.get_metrics(),
            "robots": {
                "enabled": self.robots_cache is not None,
                **self.robots_stats.as_dict(),
            },
            "deduplicator": {
                "backend": type(self.deduplicator).__name__,
                "persistent": isinstance(self.deduplicator, PersistentMinHashDeduplicator),
            },
        }
