"""
Federated Search Aggregator.

Orchestrates multi-provider search queries concurrently, performing
RRF ranking, URL normalization, and result quality scoring.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from searchops.search.contracts import SearchQuery, SearchResultItem

log = structlog.get_logger(__name__)


class FederatedSearchAggregator:
    """Aggregates multi-provider search results with RRF and Quality Scoring."""

    def __init__(self, providers: list[Any] | None = None) -> None:
        # Legacy initializer parameter maintained for compatibility
        pass

    def normalize_url(self, url: str) -> str:
        """Strip trailing slash, lowercase hostname, remove basic tracking parameters."""
        try:
            url_clean = url.strip().lower().rstrip("/")
            # Strip query params like utm_source, utm_medium, etc.
            url_clean = re.sub(r"\?utm_[^&]+(&utm_[^&]+)*", "", url_clean)
            return url_clean
        except Exception:
            return url.lower()

    def fuse_results(self, results_nested: list[list[SearchResultItem]]) -> list[SearchResultItem]:
        """Performs Reciprocal Rank Fusion (RRF) and Result Quality Scoring."""
        RRF_K = 60.0
        rrf_scores: dict[str, float] = {}
        items_by_url: dict[str, SearchResultItem] = {}

        # 1. RRF Calculation
        for res_list in results_nested:
            if not res_list:
                continue
            for rank, item in enumerate(res_list, start=1):
                norm_url = self.normalize_url(item.url)
                if norm_url not in items_by_url:
                    items_by_url[norm_url] = item
                    rrf_scores[norm_url] = 0.0

                # Accumulate rank score: 1 / (K + rank)
                rrf_scores[norm_url] += 1.0 / (RRF_K + rank)

        # 2. Result Quality Scoring
        final_results: list[SearchResultItem] = []
        for norm_url, item in items_by_url.items():
            base_score = rrf_scores[norm_url]

            # Domain authority boost
            domain_boost = 1.0
            url_lower = item.url.lower()
            if any(domain in url_lower for domain in ("github.com", "docs.", ".gov", ".edu", "arxiv.org")):
                domain_boost = 1.25

            # Content length score
            length_boost = 1.0
            snippet_len = len(item.snippet)
            if snippet_len > 150:
                length_boost = 1.15
            elif snippet_len < 40:
                length_boost = 0.8  # penalty for short fragments

            # Adjust final score
            item.score = round(base_score * domain_boost * length_boost, 6)
            final_results.append(item)

        final_results.sort(key=lambda x: x.score, reverse=True)
        return final_results

    async def aggregate_search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Legacy entrypoint for backward compatibility. Delegating to SearchOrchestrator."""
        from searchops.search.orchestrator import search_orchestrator

        # Trigger dynamic plugin discovery on demand to auto-register Serper/Tavily
        from searchops.search.registry import registry

        registry.discover_plugins()

        results = await search_orchestrator.search(query)

        # A search that found nothing must return nothing.
        #
        # This used to synthesise two plausible-looking results
        # (docs.searchops.dev/research/<hash>, github.com/searchops/...) when
        # every provider failed. Those URLs do not exist — they resolve to DNS
        # failures — so the pipeline marked 3 "sources" as scraped, all failed,
        # and produced a report citing sources that were never read. Returning
        # [] lets the critic/verifier emit an honest gap instead, and the job
        # reports that no sources were found.
        if not results:
            log.warning(
                "aggregate_search.no_results",
                query=query.query,
                reason="all providers returned no results; not synthesising",
            )
            return []

        return results[: query.max_results]
