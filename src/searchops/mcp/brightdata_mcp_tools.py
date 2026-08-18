"""
Bright Data MCP Tools — Premium Tier.

Exposes Bright Data capabilities as callable tools for research agents
via the MCP (Model Context Protocol) framework.

Available tools:
1. brightdata_serp_search  — Google SERP via BD SERP API with PAA expansion
2. brightdata_unlocker_scrape — CAPTCHA-bypass scraping via Web Unlocker
3. brightdata_dataset_fetch — Pre-parsed entity data (GitHub/LinkedIn/Reddit)

Tool activation requires:
- BRIGHTDATA_API_KEY set in environment
- BRIGHTDATA_CUSTOMER_ID + BRIGHTDATA_ZONE_PASSWORD for scraping tools
- Feature flag brightdata_datasets_enabled = True
"""

from __future__ import annotations

import structlog
from typing import Any

from searchops.config.settings import get_settings
from searchops.core.interfaces.scraper import ScrapeRequest, ScrapeMode

log = structlog.get_logger(__name__)


# ────────────────────────────────────────────────────────────────────────────
#  Tool: brightdata_serp_search
# ────────────────────────────────────────────────────────────────────────────

async def brightdata_serp_search(
    query: str,
    country: str = "us",
    num_results: int = 10,
) -> dict[str, Any]:
    """
    Search Google via Bright Data SERP API.

    Returns organic results plus People-Also-Ask sub-queries and Knowledge
    Graph data for recursive deep-search expansion.

    Args:
        query:       Search query string.
        country:     2-letter ISO country code for geo-targeted results (default: "us").
        num_results: Number of organic results to return (max 20).

    Returns:
        dict with keys:
            - results: list of {title, url, snippet, rank}
            - paa_subqueries: list of PAA question strings
            - knowledge_graph: dict of entity card data
            - related_searches: list of related query strings
    """
    from searchops.search.providers.brightdata_serp import BrightDataSerpProvider
    from searchops.search.contracts import SearchQuery
    from searchops.search.domain.models import SearchProfile

    cfg = get_settings()
    if not cfg.search.brightdata_api_key:
        return {"error": "BRIGHTDATA_API_KEY not configured", "results": []}

    provider = BrightDataSerpProvider()
    search_query = SearchQuery(
        query=query,
        max_results=num_results,
        profile=SearchProfile.PREMIUM,
        raw_metadata={"country": country},
    )

    try:
        items = await provider.search(search_query)
        paa: list[str] = []
        kg: dict = {}
        related: list[str] = []

        if items:
            meta = items[0].raw_metadata
            paa = meta.get("paa_subqueries", [])
            kg = meta.get("knowledge_graph", {})
            related = meta.get("related_searches", [])

        return {
            "results": [
                {
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                    "rank": item.rank,
                }
                for item in items
            ],
            "paa_subqueries": paa,
            "knowledge_graph": kg,
            "related_searches": related,
            "total": len(items),
        }
    except Exception as exc:
        log.error("mcp.brightdata_serp_search: failed", error=str(exc))
        return {"error": str(exc), "results": []}


# ────────────────────────────────────────────────────────────────────────────
#  Tool: brightdata_unlocker_scrape
# ────────────────────────────────────────────────────────────────────────────

async def brightdata_unlocker_scrape(
    url: str,
    extract_markdown: bool = True,
    wait_for_selector: str | None = None,
) -> dict[str, Any]:
    """
    Scrape a CAPTCHA-protected or bot-blocked URL via Bright Data Web Unlocker.

    Ideal for LinkedIn, Cloudflare-protected sites, and any target that returns
    HTTP 403/429 to standard scrapers.

    Args:
        url:               The URL to scrape.
        extract_markdown:  If True, returns pruned Markdown; otherwise raw HTML.
        wait_for_selector: Optional CSS selector to wait for before capturing.

    Returns:
        dict with keys:
            - url, final_url, status_code
            - markdown (if extract_markdown=True)
            - html (raw HTML)
            - scrape_mode_used
            - error (if failed)
    """
    from searchops.scraping.brightdata_unlocker import build_bd_unlocker

    cfg = get_settings()
    scraper = build_bd_unlocker(cfg.scraping)
    if scraper is None:
        return {
            "error": "Bright Data Unlocker credentials not configured. "
                     "Set BRIGHTDATA_CUSTOMER_ID and BRIGHTDATA_ZONE_PASSWORD.",
            "status_code": 0,
        }

    request = ScrapeRequest(
        url=url,
        mode=ScrapeMode.BD_UNLOCKER,
        extract_markdown=extract_markdown,
        wait_for_selector=wait_for_selector,
    )

    try:
        result = await scraper.scrape(request)
        return {
            "url": result.url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "markdown": result.markdown,
            "html": result.html[:5000] if result.html else None,  # Truncate for MCP response size
            "scrape_mode_used": result.scrape_mode_used,
            "duration_ms": result.duration_ms,
        }
    except Exception as exc:
        log.error("mcp.brightdata_unlocker_scrape: failed", url=url, error=str(exc))
        return {"error": str(exc), "url": url, "status_code": 500}


# ────────────────────────────────────────────────────────────────────────────
#  Tool: brightdata_dataset_fetch
# ────────────────────────────────────────────────────────────────────────────

async def brightdata_dataset_fetch(
    target_type: str,
    url: str,
) -> dict[str, Any]:
    """
    Fetch pre-parsed structured data from Bright Data's Web Scraper APIs.

    Returns clean JSON entity data without requiring HTML scraping.
    Token-efficient: 70-90% fewer tokens than raw-page scraping for LLM context.

    Args:
        target_type: Dataset type. One of:
                     "github_repo", "linkedin_company", "linkedin_profile",
                     "crunchbase_org", "reddit_thread", "hackernews_item"
        url:         The target entity URL to fetch data for.

    Returns:
        Structured JSON dict for the requested entity.

    Examples:
        brightdata_dataset_fetch("github_repo", "https://github.com/langchain-ai/langgraph")
        brightdata_dataset_fetch("linkedin_company", "https://www.linkedin.com/company/openai")
        brightdata_dataset_fetch("reddit_thread", "https://www.reddit.com/r/MachineLearning/...")
    """
    from searchops.scraping.brightdata_datasets import build_bd_dataset_client

    cfg = get_settings()
    client = build_bd_dataset_client(cfg.scraping)
    if client is None:
        return {
            "error": "Bright Data API key not configured. Set BRIGHTDATA_API_KEY.",
            "target_type": target_type,
            "url": url,
        }

    try:
        return await client.fetch(target_type, url)
    except Exception as exc:
        log.error(
            "mcp.brightdata_dataset_fetch: failed",
            target_type=target_type,
            url=url,
            error=str(exc),
        )
        return {"error": str(exc), "target_type": target_type, "url": url}


# ────────────────────────────────────────────────────────────────────────────
#  Tool registry for MCP framework integration
# ────────────────────────────────────────────────────────────────────────────

BRIGHTDATA_MCP_TOOLS = {
    "brightdata_serp_search": brightdata_serp_search,
    "brightdata_unlocker_scrape": brightdata_unlocker_scrape,
    "brightdata_dataset_fetch": brightdata_dataset_fetch,
}

__all__ = [
    "brightdata_serp_search",
    "brightdata_unlocker_scrape",
    "brightdata_dataset_fetch",
    "BRIGHTDATA_MCP_TOOLS",
]
