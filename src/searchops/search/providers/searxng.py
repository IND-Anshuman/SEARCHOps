"""
SearXNG Local Fallback Search Provider.
"""

from __future__ import annotations

import httpx
import structlog
from typing import Any

from searchops.config.settings import Settings, get_settings
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability

log = structlog.get_logger(__name__)


class SearXNGSearchProvider(ISearchProvider):
    """SearXNG self-hosted Search Engine implementation."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.search
        self.base_url = self.settings.searxng_base_url.rstrip("/")
        self.verify_ssl = self.settings.searxng_verify_ssl

    @property
    def name(self) -> str:
        return "searxng"

    @property
    def capabilities(self) -> set[SearchCapability]:
        return {
            SearchCapability.KEYWORD,
            SearchCapability.NEWS,
            SearchCapability.METADATA,
        }

    @property
    def cost_per_query(self) -> float:
        return 0.0000  # Self-hosted, free!

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform search query via SearXNG JSON API."""
        clean_query = query.query.replace('"', '').strip()
        if not clean_query:
            return []

        url = f"{self.base_url}/"
        params = {
            "q": clean_query,
            "format": "json",
            "pageno": 1
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.search_timeout, verify=self.verify_ssl) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    log.error("SearXNG search failed", status=resp.status_code, body=resp.text[:200])
                    return []

                results = resp.json().get("results", [])
                items: list[SearchResultItem] = []
                for item in results:
                    items.append(
                        SearchResultItem(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("content", ""),
                            score=1.0,
                            provider=self.name,
                            raw_metadata=item,
                        )
                    )
                return items
        except Exception as exc:
            log.error("SearXNG provider search failure", error=str(exc))
            return []
