"""
Tavily Search Provider Implementation.
"""

from __future__ import annotations

from typing import Any
import httpx
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability

log = structlog.get_logger(__name__)


class TavilySearchProvider(ISearchProvider):
    """Tavily Search API implementation."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.search
        self.api_key = self.settings.tavily_api_key.get_secret_value() if self.settings.tavily_api_key else None

    @property
    def name(self) -> str:
        return "tavily"

    @property
    def capabilities(self) -> set[SearchCapability]:
        return {
            SearchCapability.SEMANTIC,
            SearchCapability.NEWS,
            SearchCapability.METADATA,
            SearchCapability.MARKDOWN,
        }

    @property
    def cost_per_query(self) -> float:
        return 0.0015

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform search using Tavily API."""
        if not self.api_key:
            log.warning("Tavily API key not configured — skipping provider")
            return []

        clean_query = query.query.replace('"', '').strip()
        if not clean_query:
            log.warning("Tavily query empty — skipping search")
            return []

        # Tavily API expects search_depth to be 'basic' or 'advanced'
        tavily_depth = "advanced" if query.search_depth in ("deep", "advanced") else "basic"

        payload: dict[str, Any] = {
            "api_key": self.api_key,
            "query": clean_query,
            "max_results": min(query.max_results, 20),
            "search_depth": tavily_depth,
        }
        if query.include_domains:
            payload["include_domains"] = query.include_domains
        if query.exclude_domains:
            payload["exclude_domains"] = query.exclude_domains

        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json=payload,
                )
                if resp.status_code != 200:
                    log.error("Tavily search API returned error", status=resp.status_code, body=resp.text[:200])
                    return []

                results = resp.json().get("results", [])
                items: list[SearchResultItem] = []
                for item in results:
                    items.append(
                        SearchResultItem(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("content", ""),
                            score=item.get("score", 1.0),
                            provider=self.name,
                            published_date=item.get("published_date"),
                            raw_metadata=item,
                        )
                    )
                return items
        except Exception as exc:
            log.error("Tavily search provider failed", error=str(exc))
            return []
