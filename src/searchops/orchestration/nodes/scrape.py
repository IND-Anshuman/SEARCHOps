"""
Scrape node: runs the scraping pipeline over a crawl frontier.

Two modes, one node:

* **precision** — scrape the URLs search handed us (free tier: at most 3).
  No link following.
* **corpus** — drive a BFS frontier so the crawler follows in-scope links
  from each fetched page, up to the plan's ``max_pages`` / ``max_depth``.

The frontier is what makes corpus mode a crawler rather than a fixed-list
scraper. A failed page is recorded and the crawl continues: one dead link must
never truncate a dataset.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from searchops.core.interfaces.scraper import ScrapeRequest
from searchops.knowledge.evaluation.relevance import filter_by_relevance
from searchops.orchestration.passages import MAX_PAGE_CHARS
from searchops.scraping.deduplicator import build_deduplicator
from searchops.scraping.frontier import CrawlConfig, CrawlFrontier
from searchops.scraping.page_title import infer_title

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState
    from searchops.scraping.factory import PageFetcher

log = structlog.get_logger(__name__)

_MAX_URLS = 3  # free tier: cap total URLs per run
_DEDUP_THRESHOLD = 0.85  # Jaccard: near-duplicate pages above this are dropped
_MIN_DEDUP_CHARS = 200  # too little text to fingerprint reliably — never dedup it


class _InMemorySeen:
    """Stand-in for the Redis-backed seen-set.

    The frontier requires *a* visited set; when the pipeline has not been
    given one (the common case for a short research run) an in-process set
    gives the same exact de-duplication without needing Redis.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def seen(self, url: str, *, force: bool = False) -> bool:
        if url in self._seen:
            return True
        self._seen.add(url)
        return False


async def scrape_node(
    state: ResearchState,
    *,
    pipeline: PageFetcher,
) -> ResearchState:
    """Scrape ``urls_to_scrape``, crawling further when the plan says to."""
    plan = state.get("scrape_plan")
    if plan:
        config = CrawlConfig.from_scrape_plan(plan)
        max_pages = config.max_pages if config.max_pages > 0 else _MAX_URLS
    else:
        config = CrawlConfig()
        max_pages = _MAX_URLS  # free-tier default cap when no scrape_plan is configured

    fetched = {d.get("url") for d in state.get("scraped_contents", []) or [] if isinstance(d, dict)}
    urls = [u for u in state.get("urls_to_scrape", []) if u not in fetched][:max_pages]
    log.info("Scrape node executing", seed_count=len(urls), max_pages=max_pages)

    # Reuse the pipeline's own seen-set when it has one, so the frontier and
    # the pipeline agree on what has already been fetched.
    seen: Any = getattr(pipeline, "url_seen", None) or _InMemorySeen()
    frontier = CrawlFrontier(seen=seen, config=config)

    scraped: list[dict[str, Any]] = []
    failed: list[str] = []
    concurrency = min(max(max_pages // 2, 4), 10)

    # Near-duplicate guard: syndicated articles and mirror pages return
    # near-identical text under different URLs. Dropping them here — before any
    # page reaches ``extract_evidence_node`` — saves 2-4 redundant LLM
    # extraction calls per run. Best-effort: if datasketch is unavailable the
    # deduplicator is simply off and every page is kept.
    try:
        deduplicator: Any = build_deduplicator(threshold=_DEDUP_THRESHOLD)
    except Exception as exc:  # pragma: no cover - optional dependency
        log.warning("Scrape dedup unavailable; keeping all pages", error=str(exc))
        deduplicator = None
    duplicates_dropped = 0

    def _is_near_duplicate(url: str, text: str) -> bool:
        """True (and index the page) when *text* near-duplicates an earlier page.

        Runs synchronously between awaits, so the shared deduplicator needs no
        lock: index + query is atomic within the event loop.
        """
        nonlocal duplicates_dropped
        if deduplicator is None or len(text) < _MIN_DEDUP_CHARS:
            return False
        try:
            is_dup, matched = deduplicator.is_duplicate(text, threshold=_DEDUP_THRESHOLD)
            if is_dup:
                duplicates_dropped += 1
                log.info("Scrape: dropped near-duplicate page", url=url, matched=matched[:5])
                return True
            deduplicator.index_document(url, text)
        except Exception as exc:  # pragma: no cover - dedup must never fail a scrape
            log.warning("Scrape dedup check failed; keeping page", url=url, error=str(exc))
        return False

    async def _fetch(page_url: str) -> dict[str, Any] | None:
        try:
            result = await pipeline.execute(ScrapeRequest(url=page_url))
        except Exception as exc:
            log.warning("Scrape failed", url=page_url, error=str(exc))
            return None
        # Discover links even from a failed page: a 404 can still be a
        # hub whose children are worth crawling, and abandoning its links
        # would let one dead page truncate the corpus.
        await frontier.record_links(page_url, result.html or result.markdown or "")
        if result.status_code != 200:
            return None
        raw = result.markdown or result.html or ""
        if not raw.strip():
            return None
        return {
            "url": page_url,
            # Keep the page, not its first screenful: extraction retrieves
            # the passages relevant to the question from the whole page.
            "content": raw[:MAX_PAGE_CHARS],
            # Not every engine reports a title; an untitled page is cited as a bare URL.
            "title": infer_title(result.title, result.html, result.markdown),
        }

    await frontier.add_seeds(urls)

    # Worker pool over the frontier. Each worker pops the next URL, fetches
    # it, and loops; links a page discovers are enqueued as it completes, so
    # workers that find the queue momentarily empty wait for in-flight pages
    # before concluding the crawl is done.
    in_flight = 0
    progress = asyncio.Event()

    async def _worker() -> None:
        nonlocal in_flight
        while True:
            page = await frontier.next_url()
            if page is None:
                if in_flight == 0:
                    return
                progress.clear()
                await progress.wait()
                continue
            in_flight += 1
            try:
                outcome = await _fetch(page.url)
            finally:
                in_flight -= 1
                progress.set()
            if outcome is None:
                failed.append(page.url)
            elif _is_near_duplicate(page.url, outcome.get("content") or ""):
                # Near-duplicate of a page already kept this run — drop it so
                # the extractor never pays to read the same text twice.
                continue
            else:
                scraped.append(outcome)

    await asyncio.gather(*(_worker() for _ in range(concurrency)))

    # Stage 4 — deterministic relevance filter (corpus mode only). Free
    # BM25 scoring drops off-topic pages *before* any LLM call sees them, so
    # the crawl's volume is never paid for in tokens.
    if config.expands and state.get("query"):
        scraped, _dropped, relevance = filter_by_relevance(scraped, str(state["query"]), top_k=config.max_pages)
    else:
        relevance = {"pages_kept": len(scraped), "pages_dropped": 0, "degraded": "filter_off"}

    log.info(
        "Scrape complete",
        scraped=len(scraped),
        failed=len(failed),
        duplicates_dropped=duplicates_dropped,
        **frontier.stats_dict(),
    )
    return {
        "scraped_contents": [{**d, "content": ""} for d in scraped],
        "round_documents": scraped,
        "failed_urls": failed,
        "frontier_stats": {**frontier.stats_dict(), "duplicates_dropped": duplicates_dropped},
        "relevance_stats": relevance,
    }
