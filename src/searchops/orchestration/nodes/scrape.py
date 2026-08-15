"""
Scrape node: runs the scraping pipeline concurrently over urls_to_scrape.

Free-tier note: at most 3 URLs are scraped per run and content is
truncated to MAX_DOC_CHARS to limit downstream LLM token usage.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from searchops.core.interfaces.scraper import ScrapeRequest
from searchops.llm.token_budget import MAX_DOC_CHARS
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.pipeline import ScrapingPipeline

log = structlog.get_logger(__name__)

_MAX_CONCURRENT = 2   # free tier: 2 parallel scrapes max
_MAX_URLS = 3         # free tier: cap total URLs per run


async def scrape_node(
    state: ResearchState,
    *,
    pipeline: ScrapingPipeline,
) -> ResearchState:
    """Concurrently scrape urls_to_scrape (max 3) and populate scraped_contents."""
    urls = state.get("urls_to_scrape", [])[:_MAX_URLS]  # free-tier cap
    log.info("Scrape node executing", url_count=len(urls))

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _scrape_one(url: str) -> dict[str, Any] | None:
        async with semaphore:
            result = await pipeline.execute(ScrapeRequest(url=url))
            if result.status_code == 200:
                raw = result.markdown or result.html or ""
                return {
                    "url": url,
                    "content": raw[:MAX_DOC_CHARS],  # truncate before LLM
                    "title": result.title or "",
                }
            return None

    tasks = [_scrape_one(url) for url in urls]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    scraped: list[dict[str, Any]] = []
    failed: list[str] = []

    for url, res in zip(urls, raw_results):
        if isinstance(res, Exception) or res is None:
            failed.append(url)
        else:
            scraped.append(res)

    log.info("Scrape complete", scraped=len(scraped), failed=len(failed))
    return {"scraped_contents": scraped, "failed_urls": failed}  # type: ignore[misc]
