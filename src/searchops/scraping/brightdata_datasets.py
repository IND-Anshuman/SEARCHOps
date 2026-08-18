"""
Bright Data Dataset API Client — Premium Tier.

Provides pre-parsed, structured JSON payloads for specific target domains
via Bright Data's Web Scraper APIs. Unlike raw scraping, these endpoints
return clean entity data without any HTML/JS processing on the client side.

Benefits:
- 70-90% reduction in LLM token consumption vs raw-page scraping
- Structured data: ready for Knowledge Graph ingestion
- No scraping pipeline involvement — direct API call
- Supported targets: GitHub, LinkedIn, Crunchbase, Reddit, HackerNews

API docs: https://docs.brightdata.com/datasets/web-scraper-api
"""

from __future__ import annotations

import structlog
from typing import Any

import httpx

from searchops.config.subsystems.scraping import ScrapingSettings

log = structlog.get_logger(__name__)

_BD_DATASET_BASE = "https://api.brightdata.com/datasets/v3"

# Known dataset endpoint suffixes
_DATASET_ENDPOINTS: dict[str, str] = {
    "github_repo":          "/github/repository",
    "linkedin_company":     "/linkedin/company",
    "linkedin_profile":     "/linkedin/person",
    "crunchbase_org":       "/crunchbase/organization",
    "reddit_thread":        "/reddit/thread",
    "hackernews_item":      "/hackernews/item",
    "twitter_profile":      "/twitter/user",
    "amazon_product":       "/amazon/product",
}


class BrightDataDatasetClient:
    """
    Async client for Bright Data pre-parsed Web Scraper APIs.

    Each method returns a structured dict (or list of dicts) that can be
    fed directly into Knowledge Graph extractors or LLM context.
    """

    def __init__(self, cfg: ScrapingSettings) -> None:
        if not cfg.brightdata_api_key:
            raise ValueError(
                "BrightDataDatasetClient requires BRIGHTDATA_API_KEY to be configured."
            )
        self._api_key = cfg.brightdata_api_key.get_secret_value()
        self._timeout = cfg.request_timeout

    # ------------------------------------------------------------------ #
    #  Generic fetch                                                       #
    # ------------------------------------------------------------------ #

    async def fetch(self, dataset_type: str, url: str) -> dict[str, Any]:
        """
        Generic fetch for any supported dataset type.

        Args:
            dataset_type: One of the keys in _DATASET_ENDPOINTS.
            url: The target entity URL (e.g. GitHub repo URL, LinkedIn company URL).

        Returns:
            Parsed JSON dict from the Bright Data Dataset API.
        """
        endpoint_suffix = _DATASET_ENDPOINTS.get(dataset_type)
        if not endpoint_suffix:
            raise ValueError(
                f"Unknown dataset_type '{dataset_type}'. "
                f"Supported: {list(_DATASET_ENDPOINTS.keys())}"
            )

        api_url = f"{_BD_DATASET_BASE}{endpoint_suffix}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"url": url}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(api_url, headers=headers, json=payload)

            if resp.status_code != 200:
                log.error(
                    "bd_datasets.fetch: API error",
                    dataset_type=dataset_type,
                    url=url,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return {"error": f"HTTP {resp.status_code}", "url": url}

            data = resp.json()
            log.info(
                "bd_datasets.fetch: success",
                dataset_type=dataset_type,
                url=url,
            )
            return data if isinstance(data, dict) else {"results": data}

        except Exception as exc:
            log.error("bd_datasets.fetch: exception", dataset_type=dataset_type, url=url, error=str(exc))
            return {"error": str(exc), "url": url}

    # ------------------------------------------------------------------ #
    #  Domain-specific convenience methods                                 #
    # ------------------------------------------------------------------ #

    async def get_github_repo(self, repo_url: str) -> dict[str, Any]:
        """
        Fetch GitHub repository metadata: stars, forks, topics, language,
        open issues, last commit, top contributors.

        Example URL: https://github.com/langchain-ai/langgraph
        """
        return await self.fetch("github_repo", repo_url)

    async def get_linkedin_company(self, company_url: str) -> dict[str, Any]:
        """
        Fetch LinkedIn company data: headcount, growth, specialties,
        funding stage, headquarters, founded year.

        Example URL: https://www.linkedin.com/company/openai
        """
        return await self.fetch("linkedin_company", company_url)

    async def get_linkedin_profile(self, profile_url: str) -> dict[str, Any]:
        """
        Fetch LinkedIn person profile: current role, experience, education,
        skills, and connection count.
        """
        return await self.fetch("linkedin_profile", profile_url)

    async def get_crunchbase_org(self, org_url: str) -> dict[str, Any]:
        """
        Fetch Crunchbase organization: funding rounds, investors, valuation,
        key executives, and tech stack.

        Example URL: https://www.crunchbase.com/organization/anthropic
        """
        return await self.fetch("crunchbase_org", org_url)

    async def get_reddit_thread(self, thread_url: str) -> dict[str, Any]:
        """
        Fetch Reddit thread: top comments, upvote counts, author sentiment,
        and community reactions.

        Example URL: https://www.reddit.com/r/MachineLearning/comments/...
        """
        return await self.fetch("reddit_thread", thread_url)

    async def get_hackernews_item(self, item_url: str) -> dict[str, Any]:
        """
        Fetch HackerNews discussion: score, comment count, author, and
        top-level comment threads.

        Example URL: https://news.ycombinator.com/item?id=12345678
        """
        return await self.fetch("hackernews_item", item_url)


def build_bd_dataset_client(cfg: ScrapingSettings) -> BrightDataDatasetClient | None:
    """
    Factory: returns a configured BrightDataDatasetClient or None if
    BRIGHTDATA_API_KEY is not set.
    """
    if cfg.brightdata_api_key:
        try:
            return BrightDataDatasetClient(cfg)
        except Exception as exc:
            log.warning("bd_datasets: failed to build client", error=str(exc))
    return None
