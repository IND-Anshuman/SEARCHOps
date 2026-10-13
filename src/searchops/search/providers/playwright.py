"""Playwright Browser Scraper Search Provider.

Runs a local Chromium against a public engine — no API key required. This is
the key-free fallback that keeps the framework self-sufficient.

Engine selection is honoured from ``PLAYWRIGHT_SEARCH_ENGINE`` (default
``bing``). DuckDuckGo is no longer a usable default: ``html.duckduckgo.com``
and ``lite.duckduckgo.com`` both serve anti-bot challenge pages to headless
browsers (HTTP 202 / connection reset), so they return zero ``.result``
elements. Bing returns real results and its ``bing.com/ck/a`` redirect URLs
decode to the true destination.

Redirect decoding matters: the raw href is ``bing.com/ck/a?...&u=<base64>``,
where the base64 payload is the real target URL. Handing that opaque wrapper
to the scraper would make every downstream fetch fail, so it is decoded here
before the result leaves this provider.
"""

from __future__ import annotations

import base64
import binascii
import urllib.parse

import structlog

from searchops.config.settings import Settings, get_settings
from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability

log = structlog.get_logger(__name__)

#: Engines this provider knows how to scrape. Kept as data so adding an engine
#: is a table entry, not a new code branch.
_ENGINES: dict[str, dict[str, str]] = {
    "bing": {
        "url": "https://www.bing.com/search?q={q}&count=30",
        "container": "li.b_algo",
        "title": "li.b_algo h2 a",
        "redirect_host": "bing.com",
    },
    # Retained for environments where DuckDuckGo does not challenge headless
    # traffic (residential IP, or a future endpoint change). Not the default
    # because it returns zero results from a datacenter IP.
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={q}",
        "container": ".result",
        "title": ".result__a",
        "redirect_host": "duckduckgo.com",
    },
}

#: Full UA string. The truncated one in BrowserManager was enough to be
#: flagged; a complete modern Chrome UA matches what passes the challenge.
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_JS_EXTRACT = """(els, cap) => els.slice(0, cap).map(el => {
    const box = el.closest('li.b_algo') || el.closest('.result') || el.parentElement;
    const snip = box ? box.querySelector('.b_caption p, .result__snippet') : null;
    const cite = box ? box.querySelector('cite') : null;
    return {
        title: (el.textContent || '').trim(),
        href: el.href || '',
        snippet: snip ? (snip.textContent || '').trim() : '',
        cite: cite ? (cite.textContent || '').trim() : ''
    };
})"""


class PlaywrightSearchProvider(ISearchProvider):
    """Playwright Chromium Browser-driven Search Engine implementation."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.settings = cfg.search
        engine = self.settings.playwright_search_engine.lower()
        # An unknown engine falls back to the known-good default rather than
        # building a broken URL — but logs loudly so the misconfig is visible.
        if engine not in _ENGINES:
            log.warning(
                "Unknown playwright_search_engine; falling back to bing",
                configured=engine,
                supported=sorted(_ENGINES),
            )
            engine = "bing"
        self.engine_name = engine
        self._engine = _ENGINES[engine]

    #: Runs a local Chromium against a public engine — no API key required.
    requires_credentials = False

    @property
    def name(self) -> str:
        return "playwright"

    @property
    def capabilities(self) -> set[SearchCapability]:
        # Declares SEMANTIC so this provider is eligible for the DEEP profile
        # (semantic + keyword + metadata). A browser SERP does return ranked
        # results; claiming only keyword/metadata made the free fallback
        # unreachable for deep queries.
        return {
            SearchCapability.KEYWORD,
            SearchCapability.SEMANTIC,
            SearchCapability.JAVASCRIPT,
            SearchCapability.METADATA,
        }

    @property
    def cost_per_query(self) -> float:
        return 0.0005  # Slight local compute cost approximation

    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform browser search scraping via the configured engine."""
        clean_query = query.query.replace('"', "").strip()
        if not clean_query:
            return []

        # A dedicated context with a full UA: the shared context reuses a
        # session that may carry challenge cookies from a prior run.
        from playwright.async_api import async_playwright

        pw = None
        browser = None
        try:
            # start() and launch() are both outside the inner try in the old
            # code, so a browser that fails to start raised out of search()
            # and killed the search node. Every await that can fail stays
            # inside the guarded block so a dead browser degrades to [].
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=_DESKTOP_UA,
                locale="en-US",
            )
            page = await context.new_page()

            url = self._engine["url"].format(q=urllib.parse.quote_plus(clean_query))
            log.info("Performing browser search scraping", engine=self.engine_name, url=url)
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")

            # Wait for the first result container. A timeout here is a normal
            # "no results / challenge page" outcome, not an error to raise.
            try:
                await page.wait_for_selector(self._engine["container"], timeout=8000)
            except Exception:
                log.warning(
                    "Browser search returned no result containers",
                    engine=self.engine_name,
                    selector=self._engine["container"],
                )
                return []

            # Pull every field in one JS pass — avoids N round-trips per result
            # and the per-element inner_text calls that were timing out.
            raw_items = await page.locator(self._engine["title"]).evaluate_all(_JS_EXTRACT, query.max_results)

            items: list[SearchResultItem] = []
            for raw in raw_items:
                href = self._resolve_url(raw.get("href", ""))
                title = raw.get("title", "")
                if href and title:
                    items.append(
                        SearchResultItem(
                            title=title,
                            url=href,
                            snippet=raw.get("snippet", ""),
                            score=0.80,  # browser fallback score
                            provider=self.name,
                            raw_metadata={
                                "cite": raw.get("cite", ""),
                                "engine": self.engine_name,
                            },
                        )
                    )

            log.info("Browser search produced results", engine=self.engine_name, count=len(items))
            return items
        except Exception as exc:
            log.error("Playwright browser search failure", engine=self.engine_name, error=str(exc))
            return []
        finally:
            if browser:
                await browser.close()
            if pw is not None:
                await pw.stop()

    def _resolve_url(self, href: str) -> str:
        """Decode an engine redirect wrapper to the real target URL.

        Bing wraps results as ``https://www.bing.com/ck/a?...&u=a1<base64>``
        where the base64 payload is the actual destination. Returning the
        wrapper would send the scraper to Bing's redirect page instead of the
        source, so it is decoded here. Non-redirect hrefs pass through.
        """
        if not href:
            return ""

        host = self._engine.get("redirect_host", "")
        if host and host not in urllib.parse.urlparse(href).netloc:
            return href

        # DuckDuckGo redirect: //duckduckgo.com/y.js?uddg=<encoded>
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            uddg = qs.get("uddg", [""])[0]
            if uddg:
                return uddg

        # Bing ck/a: extract &u=a1<base64url> and decode.
        marker = "&u=a1"
        idx = href.find(marker)
        if idx != -1:
            token = href[idx + len(marker) :].split("&")[0]
            decoded = _b64url_decode(token)
            if decoded.startswith("http"):
                return decoded

        return href


def _b64url_decode(token: str) -> str:
    """Decode a base64url token, tolerating missing padding."""
    if not token:
        return ""
    s = token.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s).decode("utf-8", "ignore")
    except (binascii.Error, ValueError):
        return ""
