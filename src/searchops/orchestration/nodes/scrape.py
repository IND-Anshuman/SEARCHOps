"""
Scrape node: runs the scraping pipeline over a crawl frontier.

Two modes, one node:

* **precision** — scrape the URLs search handed us (free tier: at most 3),
  truncated to MAX_DOC_CHARS. No link following; identical to the previous
  behaviour.
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
from searchops.llm.token_budget import MAX_DOC_CHARS
from searchops.scraping.frontier import CrawlConfig, CrawlFrontier

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState
    from searchops.scraping.pipeline import ScrapingPipeline

log = structlog.get_logger(__name__)

_MAX_CONCURRENT = 2  # free tier: 2 parallel scrapes max
_MAX_URLS = 3  # free tier: cap total URLs per run


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
    pipeline: ScrapingPipeline,
) -> ResearchState:
    """Scrape ``urls_to_scrape``, crawling further when the plan says to."""
    plan = state.get("scrape_plan") or {}
    config = CrawlConfig.from_scrape_plan(plan)
    max_pages = config.max_pages if (config and config.max_pages > 0) else 25

    urls = state.get("urls_to_scrape", [])[:max_pages]
    log.info("Scrape node executing", seed_count=len(urls), max_pages=max_pages)

    # Reuse the pipeline's own seen-set when it has one, so the frontier and
    # the pipeline agree on what has already been fetched.
    seen: Any = getattr(pipeline, "url_seen", None) or _InMemorySeen()
    frontier = CrawlFrontier(seen=seen, config=config)

    scraped: list[dict[str, Any]] = []
    failed: list[str] = []
    concurrency = min(max(max_pages // 2, 4), 10)
    semaphore = asyncio.Semaphore(concurrency)

    async def _fetch(page_url: str) -> dict[str, Any] | None:
        async with semaphore:
            result = await pipeline.execute(ScrapeRequest(url=page_url))
            # Discover links even from a failed page: a 404 can still be a
            # hub whose children are worth crawling, and abandoning its links
            # would let one dead page truncate the corpus.
            await frontier.record_links(page_url, result.html or result.markdown or "")
            if result.status_code != 200:
                return None
            raw = result.markdown or result.html or ""
            return {
                "url": page_url,
                "content": raw[:MAX_DOC_CHARS],  # truncate before LLM
                "title": result.title or "",
            }

    await frontier.add_seeds(urls)

    # Fetch loop: pop the frontier until it is empty or the page cap is hit.
    while (page := await frontier.next_url()) is not None:
        outcome = await _fetch(page.url)
        if outcome is None:
            failed.append(page.url)
        else:
            scraped.append(outcome)

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
        **frontier.stats_dict(),
    )
    return {
        "scraped_contents": scraped,
        "failed_urls": failed,
        "frontier_stats": frontier.stats_dict(),
        "relevance_stats": relevance,
    }
