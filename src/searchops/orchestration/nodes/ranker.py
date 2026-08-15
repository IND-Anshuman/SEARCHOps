"""
Ranker & Deduplicator node: applies Reciprocal Rank Fusion (RRF) and Canonical URL Hash deduplication.
"""

from __future__ import annotations

import hashlib
import structlog
from urllib.parse import urlparse

from searchops.orchestration.states.research_state import ResearchState
from searchops.search.contracts import SearchResultItem

log = structlog.get_logger(__name__)


def _canonicalize_url(url: str) -> str:
    """Normalize URL by stripping query tracking params, trailing slashes, and lowercase scheme/host."""
    try:
        parsed = urlparse(url.strip().lower())
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:
        return url.strip().lower()


def rank_and_deduplicate(
    items: list[SearchResultItem],
    top_k: int = 10,
    rrf_k: int = 60,
) -> list[SearchResultItem]:
    """Execute Reciprocal Rank Fusion and canonical URL deduplication across search items."""
    if not items:
        return []

    # Map canonical URL hash -> item & RRF score
    url_to_item: dict[str, SearchResultItem] = {}
    url_to_score: dict[str, float] = {}

    for rank, item in enumerate(items):
        if not item.url:
            continue
        canon_url = _canonicalize_url(item.url)
        canon_hash = hashlib.sha256(canon_url.encode("utf-8")).hexdigest()

        # Reciprocal Rank Fusion score contribution: 1 / (k + rank)
        rrf_score = 1.0 / (rrf_k + rank + 1)
        url_to_score[canon_hash] = url_to_score.get(canon_hash, 0.0) + rrf_score

        if canon_hash not in url_to_item or item.score > url_to_item[canon_hash].score:
            url_to_item[canon_hash] = item

    # Sort items by aggregated RRF score in descending order
    sorted_hashes = sorted(url_to_score.keys(), key=lambda h: url_to_score[h], reverse=True)
    ranked_items = [url_to_item[h] for h in sorted_hashes[:top_k]]

    return ranked_items


async def ranker_node(state: ResearchState) -> ResearchState:
    """Node dispatcher for ranking and deduplicating aggregated search results."""
    search_results = state.get("search_results", [])
    max_sources = min(state.get("max_sources", 5), 10)

    log.info("Ranker node executing", total_raw_results=len(search_results), top_k=max_sources)
    ranked = rank_and_deduplicate(search_results, top_k=max_sources)

    urls = [item.url for item in ranked if item.url]
    log.info("Ranker node complete", deduplicated_results=len(ranked), unique_urls=len(urls))

    return {"urls_to_scrape": urls}  # type: ignore[misc]
