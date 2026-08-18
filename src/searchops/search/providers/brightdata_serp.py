"""
Bright Data SERP Search Provider — Premium Tier.

Uses the Bright Data SERP API to perform Google searches with:
- Multi-geo IP targeting (country-level)
- PAA (People Also Ask) sub-query extraction for recursive deep search
- Knowledge Graph structured entity cards
- Sitelinks, answer boxes, and organic results in one API call

Zone configuration: BRIGHTDATA_ZONE_SERP (default: "serp")
API docs: https://docs.brightdata.com/scraping-automation/serp-api
"""

from __future__ import annotations

import structlog
import urllib.parse
from typing import Any

import httpx

from searchops.config.settings import Settings, get_settings
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability

log = structlog.get_logger(__name__)


class BrightDataSerpProvider(ISearchProvider):
    """
    Bright Data SERP API — Premium search provider.

    Returns Google organic results plus PAA sub-queries, Knowledge Graph
    cards, and related searches stored in ``raw_metadata`` for recursive
    deep-search expansion by the orchestrator.
    """

    _BASE_URL = "https://api.brightdata.com/serp/req"

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._search_cfg = cfg.search
        self._scraping_cfg = cfg.scraping
        self._api_key: str | None = (
            self._search_cfg.brightdata_api_key.get_secret_value()
            if self._search_cfg.brightdata_api_key
            else None
        )
        self._zone: str = self._search_cfg.brightdata_zone_serp

    # ------------------------------------------------------------------ #
    #  ISearchProvider protocol                                            #
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return "brightdata_serp"

    @property
    def capabilities(self) -> set[SearchCapability]:
        return {
            SearchCapability.SEMANTIC,
            SearchCapability.KEYWORD,
            SearchCapability.NEWS,
            SearchCapability.METADATA,
            SearchCapability.FRESHNESS,
            SearchCapability.LOCALIZATION,
            SearchCapability.SERP_FEATURES,  # PAA + KG
            SearchCapability.ANTI_BOT,        # Residential IP rotation
        }

    @property
    def cost_per_query(self) -> float:
        """~$0.001 per SERP query (Bright Data CPM pricing)."""
        return 0.001

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Search via Bright Data SERP API and return normalized results."""
        if not self._api_key:
            log.warning("Bright Data API key not configured — skipping brightdata_serp provider")
            return []

        clean_query = query.query.replace('"', "").strip()
        if not clean_query:
            log.warning("brightdata_serp: empty query — skipping")
            return []

        # Extract optional geo-targeting from query metadata
        country: str = query.raw_metadata.get("country", "us")
        num_results: int = min(query.max_results, 20)

        google_url = (
            f"https://www.google.com/search"
            f"?q={urllib.parse.quote_plus(clean_query)}"
            f"&num={num_results}"
            f"&gl={country}"
            f"&hl=en"
        )

        payload: dict[str, Any] = {
            "zone": self._zone,
            "url": google_url,
            "format": "json",
        }
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._search_cfg.search_timeout) as client:
                resp = await client.post(self._BASE_URL, headers=headers, json=payload)

            if resp.status_code != 200:
                log.error(
                    "brightdata_serp: API returned non-200",
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return []

            data: dict[str, Any] = resp.json()
            return self._parse_response(data, clean_query)

        except Exception as exc:
            log.error("brightdata_serp: search failed", error=str(exc))
            return []

    # ------------------------------------------------------------------ #
    #  Response parsing                                                    #
    # ------------------------------------------------------------------ #

    def _parse_response(
        self, data: dict[str, Any], original_query: str
    ) -> list[SearchResultItem]:
        """Parse Bright Data SERP JSON into normalized SearchResultItems."""
        organic: list[dict[str, Any]] = data.get("organic", [])
        paa_raw: list[dict[str, Any]] = data.get("people_also_ask", [])
        knowledge_graph: dict[str, Any] = data.get("knowledge_graph", {})
        related_searches: list[str] = [
            r.get("query", "") for r in data.get("related_searches", [])
        ]

        # Extract PAA sub-queries for recursive deep-search expansion
        paa_subqueries: list[str] = [
            item.get("question", "") for item in paa_raw if item.get("question")
        ]

        items: list[SearchResultItem] = []
        for rank, item in enumerate(organic, start=1):
            items.append(
                SearchResultItem(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    score=1.0,
                    rank=rank,
                    provider=self.name,
                    published_date=item.get("date"),
                    raw_metadata={
                        # Store premium data for orchestrator + agents to consume
                        "paa_subqueries": paa_subqueries,
                        "knowledge_graph": knowledge_graph,
                        "related_searches": related_searches,
                        "sitelinks": item.get("sitelinks", []),
                        "original_query": original_query,
                        "position": item.get("position", rank),
                        "displayed_link": item.get("displayed_link", ""),
                    },
                )
            )

        log.info(
            "brightdata_serp: search complete",
            results=len(items),
            paa_count=len(paa_subqueries),
            has_knowledge_graph=bool(knowledge_graph),
        )
        return items
