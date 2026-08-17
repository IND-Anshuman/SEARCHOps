"""
Playwright Browser Scraper Search Provider.
"""

from __future__ import annotations

import urllib.parse
import structlog

from searchops.config.settings import Settings, get_settings
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability
from searchops.search.browser import browser_manager

log = structlog.get_logger(__name__)


class PlaywrightSearchProvider(ISearchProvider):
    """Playwright Chromium Browser-driven Search Engine implementation."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.search
        self.target_engine = self.settings.playwright_search_engine.lower()

    @property
    def name(self) -> str:
        return "playwright"

    @property
    def capabilities(self) -> set[SearchCapability]:
        return {
            SearchCapability.KEYWORD,
            SearchCapability.JAVASCRIPT,
            SearchCapability.METADATA,
        }

    @property
    def cost_per_query(self) -> float:
        return 0.0005  # Slight local compute cost approximation

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform browser search scraping via DuckDuckGo (HTML) or fallback."""
        clean_query = query.query.replace('"', '').strip()
        if not clean_query:
            return []

        page = None
        try:
            page = await browser_manager.get_page()
            
            # Use DuckDuckGo HTML version by default as it is lightweight and CAPTCHA-resistant
            encoded_q = urllib.parse.quote_plus(clean_query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
            
            log.info("Performing browser search scraping", engine="duckduckgo", url=url)
            await page.goto(url, timeout=10000, wait_until="domcontentloaded")
            
            # Wait for result containers
            await page.wait_for_selector(".result", timeout=5000)
            
            locators = page.locator(".result")
            count = await locators.count()
            
            items: list[SearchResultItem] = []
            for i in range(min(count, query.max_results)):
                loc = locators.nth(i)
                
                title_loc = loc.locator(".result__a")
                snippet_loc = loc.locator(".result__snippet")
                
                if await title_loc.count() > 0:
                    title = (await title_loc.inner_text()).strip()
                    href = await title_loc.get_attribute("href") or ""
                    
                    # Parse DuckDuckGo redirect URL if present
                    if href.startswith("//duckduckgo.com/y.js"):
                        # Extract real URL from query parameters
                        parsed = urllib.parse.urlparse(href)
                        qs = urllib.parse.parse_qs(parsed.query)
                        href = qs.get("uddg", [""])[0]
                    
                    snippet = ""
                    if await snippet_loc.count() > 0:
                        snippet = (await snippet_loc.inner_text()).strip()
                    
                    if href and title:
                        items.append(
                            SearchResultItem(
                                title=title,
                                url=href,
                                snippet=snippet,
                                score=0.80, # browser fallback score
                                provider=self.name,
                                raw_metadata={"index": i}
                            )
                        )
            
            return items
            
        except Exception as exc:
            log.error("Playwright browser search failure", error=str(exc))
            return []
        finally:
            if page:
                await browser_manager.release_page(page)
