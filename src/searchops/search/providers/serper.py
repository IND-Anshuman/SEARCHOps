"""
Serper (Google Search) Provider Implementation.
"""

from __future__ import annotations

import httpx
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability

log = structlog.get_logger(__name__)


class SerperSearchProvider(ISearchProvider):
    """Serper.dev (Google) Search API implementation."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.search
        self.api_key = self.settings.serper_api_key.get_secret_value() if self.settings.serper_api_key else None

    @property
    def name(self) -> str:
        return "serper"

    @property
    def capabilities(self) -> set[SearchCapability]:
        return {
            SearchCapability.KEYWORD,
            SearchCapability.NEWS,
            SearchCapability.LOCALIZATION,
        }

    @property
    def cost_per_query(self) -> float:
        return 0.0010

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform search using Serper API."""
        if not self.api_key:
            log.warning("Serper API key not configured — skipping provider")
            return []

        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                    json={"q": query.query, "num": query.max_results},
                )
                if resp.status_code != 200:
                    return []

                results = resp.json().get("organic", [])
                items: list[SearchResultItem] = []
                for item in results:
                    items.append(
                        SearchResultItem(
                            title=item.get("title", ""),
                            url=item.get("link", ""),
                            snippet=item.get("snippet", ""),
                            score=1.0,
                            provider=self.name,
                            raw_metadata=item,
                        )
                    )
                return items
        except Exception as exc:
            log.error("Serper search provider failed", error=str(exc))
            return []
