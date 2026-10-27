"""Choose the page fetcher for the configured tool profile (``TOOL_PROFILE``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from searchops.config.settings import get_settings

if TYPE_CHECKING:
    from searchops.infrastructure.cache.redis import RedisCache
    from searchops.scraping.pipeline import ScrapingPipeline
    from searchops.scraping.tavily_extract import TavilyExtractPipeline

    PageFetcher = ScrapingPipeline | TavilyExtractPipeline


def build_scraping_pipeline(cache: RedisCache | None = None) -> PageFetcher:
    """Tavily Extract for ``api``; the local tool ladder for ``local``.

    The local pipeline is imported only when chosen: its tiers pull in the
    optional ``local-tools`` packages, which the slim image does not install.
    """
    if get_settings().scraping.tool_profile == "api":
        from searchops.scraping.tavily_extract import TavilyExtractPipeline

        return TavilyExtractPipeline(cache=cache)
    from searchops.scraping.pipeline import ScrapingPipeline

    return ScrapingPipeline(cache=cache)
