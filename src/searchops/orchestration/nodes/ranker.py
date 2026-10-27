"""
Ranker & Deduplicator node: applies Reciprocal Rank Fusion (RRF) and Canonical URL Hash deduplication.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse

import structlog

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState
    from searchops.search.contracts import SearchResultItem

log = structlog.get_logger(__name__)


_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref", "spm", "igshid")


def _canonicalize_url(url: str) -> str:
    """Normalize a URL for de-duplication.

    Scheme and host are case-insensitive and lowercased; the path and query
    are case-sensitive on most servers and are kept as-is. Tracking params are
    dropped, but meaningful ones (``?v=``, ``?id=``) are kept — stripping every
    query param merged distinct pages into one.
    """
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.rstrip("/") or ""
        params = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith(_TRACKING_PREFIXES)]
        query = f"?{urlencode(sorted(params))}" if params else ""
        return f"{parsed.scheme.lower()}://{host}{path}{query}"
    except Exception:
        return url.strip()


def rank_and_deduplicate(
    items: list[SearchResultItem],
    top_k: int = 10,
    rrf_k: int = 60,
) -> list[SearchResultItem]:
    """Reciprocal Rank Fusion across per-query result lists, with URL dedup.

    RRF needs each item's rank *within its own result list* (stamped by the
    search node), so a URL that several sub-queries rank highly wins.
    """
    if not items:
        return []
    ranks = _per_list_ranks(items)

    # Map canonical URL hash -> item & RRF score
    url_to_item: dict[str, SearchResultItem] = {}
    url_to_score: dict[str, float] = {}

    for rank, item in zip(ranks, items, strict=True):
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


def _per_list_ranks(items: list[SearchResultItem]) -> list[int]:
    """Zero-based rank of each item within its own query's result list.

    The search node stamps ``rank`` (1-based) per query; fall back to flat
    position for items that arrive without one.
    """
    return [
        max(int(getattr(item, "rank", 0) or 0) - 1, 0) if getattr(item, "rank", 0) else i
        for i, item in enumerate(items)
    ]


async def ranker_node(state: ResearchState) -> ResearchState:
    """Node dispatcher for ranking and deduplicating aggregated search results."""
    # Rank this round's results only. Ranking the accumulated history put the
    # first round's URLs back on top every loop, so follow-up rounds
    # re-scraped the same pages.
    search_results = state.get("round_search_results")
    if search_results is None:
        search_results = state.get("search_results", [])
    already = {
        _canonicalize_url(d.get("url", "")) for d in state.get("scraped_contents", []) or [] if isinstance(d, dict)
    }
    scrape_plan = state.get("scrape_plan") or {}
    req_pages = (
        scrape_plan.get("max_pages", 15) if isinstance(scrape_plan, dict) else getattr(scrape_plan, "max_pages", 15)
    )
    max_sources = max(state.get("max_sources", 10), req_pages)

    log.info("Ranker node executing", total_raw_results=len(search_results), top_k=max_sources)
    fresh = [r for r in search_results if r.url and _canonicalize_url(r.url) not in already]
    ranked = rank_and_deduplicate(fresh, top_k=max_sources)

    urls = [item.url for item in ranked if item.url]
    log.info("Ranker node complete", deduplicated_results=len(ranked), unique_urls=len(urls))

    return {"urls_to_scrape": urls}
