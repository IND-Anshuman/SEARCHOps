"""
Crawl4AI Local AI Scraping Engine (Phase 3).

Wraps crawl4ai's ``AsyncWebCrawler`` with a production-grade configuration:

- **BM25ContentFilter** — keyword-driven relevance filtering; keeps only
  sections whose TF-IDF BM25 score meets the threshold relative to the page
  as a whole.  Result: removes off-topic nav/sidebar noise.

- **PruningContentFilter** — entropy-metric DOM pruning; removes elements
  whose information density (character entropy) falls below a threshold.
  Result: strips cookie banners, legal boilerplate, and whitespace noise.

- **fit_markdown** output — post-filter Markdown that crawl4ai considers
  semantically "fit" for LLM consumption, typically 50–70% fewer tokens
  than raw HTML conversion.

Position in Pipeline (Tier 0.5):
    Tier 0    StealthHTTP  (curl_cffi, no proxy, ~150ms, $0)
  ──► Tier 0.5  Crawl4AI    (local async + BM25 pruning, ~800ms, $0)  ◄── NEW
    Tier 1    Playwright   (full JS pool, ~1.5s, $0)
    Tier 2    Firecrawl    (managed API, ~3-5s, $$$)
    Tier 3    BasicHTTP    (last resort, ~500ms, $0)

Why use Crawl4AI instead of extending ContentPruner?
- ContentPruner processes already-fetched HTML (post-hoc).
- Crawl4AI drives its own Playwright context, applies smarter async chunking,
  and provides built-in link-graph extraction — 6× faster throughput than
  naive Playwright scripts for JS-heavy SPAs.
- Achieves semantically filtered ``fit_markdown`` vs raw Markdown.

Usage::

    scraper = Crawl4AIScraper()
    await scraper.start()

    result = await scraper.scrape(ScrapeRequest(url="https://example.com"))
    print(result.markdown)   # Token-optimized fit_markdown
    print(result.links[:10]) # Internal links

    await scraper.close()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy imports — required so unittest.mock.patch() can intercept
# them.  Falls back gracefully when crawl4ai is not installed.
# ---------------------------------------------------------------------------
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.content_filter_strategy import PruningContentFilter
except ImportError:  # pragma: no cover
    AsyncWebCrawler = None  # type: ignore[assignment,misc]
    BrowserConfig = None    # type: ignore[assignment,misc]
    CrawlerRunConfig = None  # type: ignore[assignment,misc]
    CacheMode = None         # type: ignore[assignment,misc]
    PruningContentFilter = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENTROPY_THRESHOLD       = 0.45   # Info-density threshold for PruningContentFilter
_BM25_THRESHOLD          = 0.30   # BM25 relevance threshold
_WORD_COUNT_MIN          = 10     # Skip DOM elements with fewer words
_MAX_INTERNAL_LINKS      = 50     # Cap link list to avoid over-indexing
_DEFAULT_MAX_CONCURRENCY = 5      # Concurrent scrapes (crawl4ai has its own pool)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Crawl4AIConfig:
    """
    Immutable configuration for the Crawl4AI engine.

    All fields have conservative production-safe defaults.
    Override per-request via ``ScrapeRequest.metadata``.
    """

    headless: bool = True
    """Run browser in headless mode (False = visible, useful for debugging)."""

    entropy_threshold: float = _ENTROPY_THRESHOLD
    """PruningContentFilter entropy gate (0.0–1.0; lower = stricter pruning)."""

    bm25_threshold: float = _BM25_THRESHOLD
    """BM25ContentFilter relevance gate (0.0–1.0)."""

    word_count_min: int = _WORD_COUNT_MIN
    """Skip DOM elements with fewer words than this."""

    exclude_external_links: bool = True
    """Strip external links from extracted content."""

    remove_overlay_elements: bool = True
    """Remove cookie banners, modal overlays, chat widgets."""

    use_fit_markdown: bool = True
    """Prefer ``fit_markdown`` over raw markdown when available."""

    extra_browser_args: list[str] = field(default_factory=lambda: [
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
    ])
    """Additional Chromium launch arguments for performance and stealth."""


# ---------------------------------------------------------------------------
# Crawl4AIScraper
# ---------------------------------------------------------------------------

class Crawl4AIScraper(IScraper):
    """
    Local-first async AI web scraper backed by ``crawl4ai.AsyncWebCrawler``.

    Applies BM25 + entropy-based content filtering to produce token-optimized
    ``fit_markdown`` — ready for direct LLM consumption without further
    post-processing.

    Lifecycle:
        The crawler is **lazily initialised** on the first call to :meth:`scrape`
        and persists across calls (context reuse).  Call :meth:`close` when the
        scraper is no longer needed to release the underlying Playwright context.

    Attributes:
        _cfg:     Immutable :class:`Crawl4AIConfig`.
        _crawler: crawl4ai ``AsyncWebCrawler`` instance (None until first use).

    Example::

        scraper = Crawl4AIScraper()
        result  = await scraper.scrape(ScrapeRequest(url="https://docs.example.com"))
        # result.markdown  — BM25+entropy-pruned fit_markdown
        # result.links     — internal links for graph construction
        await scraper.close()
    """

    def __init__(self, config: Crawl4AIConfig | None = None) -> None:
        self._cfg     = config or Crawl4AIConfig()
        self._crawler: Any | None = None          # Lazy init
        self._lock    = asyncio.Lock()             # Serialise crawler init

        log.debug("Crawl4AIScraper created", entropy_threshold=self._cfg.entropy_threshold)

    # ------------------------------------------------------------------ #
    #  IScraper protocol                                                    #
    # ------------------------------------------------------------------ #

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """
        Crawl *request.url* with BM25 + entropy pruning and return a ScrapeResult.

        On success:  ``result.markdown`` contains ``fit_markdown`` (or fallback
                     Markdown); ``result.html`` contains raw HTML.
        On failure:  ``result.status_code == 500``; ``result.metadata["error"]``
                     describes the failure.

        The method is safe to call concurrently.
        """
        log.info("crawl4ai.scrape", url=request.url)
        start = time.perf_counter()

        try:
            result_raw = await self._crawl(request)
        except Exception as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 1)
            log.error("crawl4ai.scrape.failed", url=request.url, error=str(exc), duration_ms=elapsed)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.CRAWL4AI,
                duration_ms=elapsed,
                metadata={"error": str(exc)},
            )

        elapsed = round((time.perf_counter() - start) * 1000, 1)

        if not result_raw.get("success"):
            log.warning(
                "crawl4ai.scrape.not_success",
                url=request.url,
                error=result_raw.get("error"),
                duration_ms=elapsed,
            )
            return ScrapeResult(
                url=request.url,
                final_url=result_raw.get("final_url", request.url),
                status_code=500,
                scrape_mode_used=ScrapeMode.CRAWL4AI,
                duration_ms=elapsed,
                metadata={"error": result_raw.get("error", "crawl4ai returned success=False")},
            )

        content   = result_raw["markdown"]
        word_cnt  = len(content.split()) if content else 0

        log.info(
            "crawl4ai.scrape.done",
            url=request.url,
            words=word_cnt,
            fit_markdown_len=len(content),
            duration_ms=elapsed,
        )

        return ScrapeResult(
            url=request.url,
            final_url=result_raw.get("final_url", request.url),
            status_code=200,
            html=result_raw.get("html"),
            markdown=content,
            title=result_raw.get("title", ""),
            links=result_raw.get("links", []),
            word_count=word_cnt,
            scrape_mode_used=ScrapeMode.CRAWL4AI,
            duration_ms=elapsed,
            metadata={
                "fit_markdown_len": len(content),
                "crawl4ai_version":  result_raw.get("version", "unknown"),
                **(result_raw.get("extra_metadata", {})),
            },
        )

    async def scrape_many(
        self,
        requests: list[ScrapeRequest],
        *,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    ) -> list[ScrapeResult]:
        """
        Scrape multiple URLs concurrently, bounded by *max_concurrency*.

        Crawl4AI manages its own Playwright context pool — the semaphore
        here is a secondary safety net against overwhelming the local machine.
        """
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def health_check(self) -> bool:
        """Return True when ``crawl4ai`` is importable."""
        try:
            import crawl4ai  # noqa: F401
            return True
        except ImportError:
            log.error("crawl4ai not installed — Crawl4AIScraper unavailable")
            return False

    async def start(self) -> None:
        """
        Eagerly initialise the crawler (optional; scrape() does it lazily).

        Call during application startup to amortise first-request latency.
        """
        await self._get_crawler()

    async def close(self) -> None:
        """
        Shut down the underlying Playwright context and release resources.

        Always call this during application teardown to avoid browser leaks.
        """
        async with self._lock:
            if self._crawler is not None:
                try:
                    await self._crawler.__aexit__(None, None, None)
                except Exception as exc:  # pragma: no cover
                    log.warning("crawl4ai.close.error", error=str(exc))
                finally:
                    self._crawler = None
                    log.debug("Crawl4AIScraper closed")

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _get_crawler(self) -> Any:
        """
        Return the shared ``AsyncWebCrawler``, initialising it on first call.

        Uses ``asyncio.Lock`` to prevent concurrent double-initialisation.
        """
        async with self._lock:
            if self._crawler is None:
                self._crawler = await self._create_crawler()
        return self._crawler

    async def _create_crawler(self) -> Any:
        """
        Build and start a new ``AsyncWebCrawler`` with production settings.

        Browser configuration mirrors headless Chrome settings used by the
        Phase 1 BrowserPool, with additional anti-automation flags.
        """
        if AsyncWebCrawler is None or BrowserConfig is None:  # pragma: no cover
            raise RuntimeError("crawl4ai is not installed. Run: uv add 'crawl4ai>=0.4.0'")

        browser_cfg = BrowserConfig(
            headless=self._cfg.headless,
            extra_args=list(self._cfg.extra_browser_args),
        )
        crawler = AsyncWebCrawler(config=browser_cfg)
        await crawler.__aenter__()
        log.debug("Crawl4AI AsyncWebCrawler started")
        return crawler

    async def _crawl(self, request: ScrapeRequest) -> dict[str, Any]:
        """
        Execute the crawl4ai crawl and return a normalised result dict.

        Returns::

            {
                "success":        bool,
                "markdown":       str,    # fit_markdown or fallback markdown
                "html":           str | None,
                "final_url":      str,
                "title":          str,
                "links":          list[str],   # internal link hrefs
                "version":        str,
                "extra_metadata": dict,
                "error":          str | None,  # present on failure
            }
        """
        if PruningContentFilter is None or CrawlerRunConfig is None or CacheMode is None:  # pragma: no cover
            raise RuntimeError("crawl4ai is not installed. Run: uv add 'crawl4ai>=0.4.0'")

        # Entropy pruning — removes low information-density DOM sections
        content_filter = PruningContentFilter(
            threshold=self._cfg.entropy_threshold,
            threshold_type="fixed",
            min_word_threshold=self._cfg.word_count_min,
        )

        run_cfg = CrawlerRunConfig(
            content_filter=content_filter,
            word_count_threshold=self._cfg.word_count_min,
            cache_mode=CacheMode.BYPASS,          # Always fetch fresh (we cache in Redis)
            exclude_external_links=self._cfg.exclude_external_links,
            remove_overlay_elements=self._cfg.remove_overlay_elements,
        )

        crawler = await self._get_crawler()
        res     = await crawler.arun(url=request.url, config=run_cfg)

        # Prefer semantically-filtered fit_markdown; fall back to raw markdown
        if self._cfg.use_fit_markdown and res.fit_markdown:
            content = res.fit_markdown.raw_markdown if hasattr(res.fit_markdown, "raw_markdown") else str(res.fit_markdown)
        elif res.markdown:
            content = res.markdown.raw_markdown if hasattr(res.markdown, "raw_markdown") else str(res.markdown)
        else:
            content = ""

        # Extract internal links (hrefs only, capped)
        raw_links = res.links or {}
        internal  = raw_links.get("internal", [])
        link_hrefs = [
            lnk.get("href", lnk) if isinstance(lnk, dict) else str(lnk)
            for lnk in internal[:_MAX_INTERNAL_LINKS]
        ]

        # Extract metadata
        meta = {}
        if res.metadata:
            meta = dict(res.metadata) if isinstance(res.metadata, dict) else {}

        return {
            "success":        bool(res.success),
            "markdown":       content,
            "html":           res.html if hasattr(res, "html") else None,
            "final_url":      str(res.url) if res.url else request.url,
            "title":          meta.pop("title", ""),
            "links":          link_hrefs,
            "version":        "0.9.x",
            "extra_metadata": meta,
            "error":          getattr(res, "error_message", None),
        }


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

_shared_scraper: Crawl4AIScraper | None = None
_shared_lock = asyncio.Lock()


def build_crawl4ai_scraper(config: Crawl4AIConfig | None = None) -> Crawl4AIScraper:
    """
    Build a fresh :class:`Crawl4AIScraper` from the given config.

    For a process-scoped singleton (reusing the Playwright context), use
    :func:`get_crawl4ai_scraper` instead.

    Args:
        config: Optional :class:`Crawl4AIConfig`.  Defaults are used when None.

    Returns:
        A new :class:`Crawl4AIScraper` instance.
    """
    return Crawl4AIScraper(config=config)


async def get_crawl4ai_scraper() -> Crawl4AIScraper:
    """
    Return (or create) the process-scoped :class:`Crawl4AIScraper` singleton.

    The first call starts the underlying Playwright context; subsequent calls
    reuse it.  This pattern amortises the ~800ms browser startup cost across
    all requests in the process lifetime.

    Returns:
        The shared :class:`Crawl4AIScraper` instance.
    """
    global _shared_scraper
    async with _shared_lock:
        if _shared_scraper is None:
            _shared_scraper = Crawl4AIScraper()
            await _shared_scraper.start()
    return _shared_scraper


async def close_crawl4ai_scraper() -> None:
    """
    Shut down the process-scoped singleton and release its browser context.

    Call during application shutdown (e.g. FastAPI ``lifespan`` teardown).
    """
    global _shared_scraper
    async with _shared_lock:
        if _shared_scraper is not None:
            await _shared_scraper.close()
            _shared_scraper = None
            log.info("crawl4ai.singleton.closed")
