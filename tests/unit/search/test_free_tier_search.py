"""Free-tier search must actually return sources.

Two regressions these guard against:

1. The Playwright provider hardcoded DuckDuckGo and declared only
   {keyword, javascript, metadata} capabilities. DuckDuckGo serves anti-bot
   challenges to headless browsers (zero results), and the missing SEMANTIC
   capability made the provider ineligible for the DEEP profile — so the
   key-free fallback could not even run on a deep query.

2. Bing wraps result URLs as bing.com/ck/a?...&u=<base64>. Handing that
   wrapper downstream sends the scraper to Bing's redirect page instead of
   the source, so every fetch fails even though search "succeeded".
"""

from __future__ import annotations

import base64
import urllib.parse
from unittest.mock import patch

import pytest

from searchops.search.contracts import SearchQuery
from searchops.search.domain.models import SearchCapability, SearchProfile
from searchops.search.providers.playwright import (
    PlaywrightSearchProvider,
    _b64url_decode,
)
from searchops.search.providers.searxng import SearXNGSearchProvider

pytestmark = pytest.mark.unit


# ── Capability eligibility ──────────────────────────────────────────────────


def test_playwright_provider_is_keyword_only():
    """Playwright is a keyword fallback, not semantic — it must not claim SEMANTIC."""
    provider = PlaywrightSearchProvider()
    assert SearchCapability.KEYWORD in provider.capabilities
    assert SearchCapability.SEMANTIC not in provider.capabilities


def test_searxng_provider_is_eligible_for_deep_profile():
    provider = SearXNGSearchProvider()
    required = {SearchCapability.SEMANTIC, SearchCapability.KEYWORD, SearchCapability.METADATA}
    assert required.issubset(provider.capabilities)


def test_free_providers_resolve_for_a_deep_query():
    """End-to-end through the registry: a DEEP query must include the free path."""
    from searchops.search.policy import policy_engine
    from searchops.search.registry import registry

    registry.discover_plugins()
    q = SearchQuery(query="api security", max_results=10, profile=SearchProfile.DEEP)
    caps = policy_engine.apply_profile(q)
    resolved = {p.name for p in registry.resolve_by_capabilities(caps)}

    # Playwright no longer claims SEMANTIC, so it is not eligible for DEEP.
    # SearXNG (which does claim SEMANTIC) remains the key-free fallback.
    assert "playwright" not in resolved, "playwright should not be eligible for DEEP"
    assert "searxng" in resolved, "key-free SearXNG fallback was filtered out"


# ── Bing redirect decoding ──────────────────────────────────────────────────


def _bing_wrap(target: str) -> str:
    """Build a bing.com/ck/a URL the way Bing actually does."""
    token = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&u=a1{token}&ntb=1"


def test_bing_redirect_decodes_to_real_url():
    provider = PlaywrightSearchProvider()
    wrapped = _bing_wrap("https://aws.amazon.com/what-is/api/")
    assert provider._resolve_url(wrapped) == "https://aws.amazon.com/what-is/api/"


def test_b64url_decode_tolerates_missing_padding():
    # 22-byte payload → needs padding that Bing omits.
    target = "https://en.m.wikipedia.org/wiki/API"
    token = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert _b64url_decode(token) == target


def test_b64url_decode_returns_empty_on_garbage():
    assert _b64url_decode("not!!base64!!") == ""


def test_non_redirect_url_passes_through_unchanged():
    provider = PlaywrightSearchProvider()
    direct = "https://owasp.org/API-Security/"
    assert provider._resolve_url(direct) == direct


def test_duckduckgo_uddg_redirect_is_decoded_by_ddg_engine():
    """The uddg branch belongs to the duckduckgo engine, not bing."""
    from searchops.config.settings import Settings

    provider = PlaywrightSearchProvider(settings=Settings(search={"playwright_search_engine": "duckduckgo"}))
    # parse_qs already percent-decodes, so encode the uddg value once.
    wrapped = "https://duckduckgo.com/y.js?uddg=" + urllib.parse.quote("https://example.com/page", safe="")
    assert provider._resolve_url(wrapped) == "https://example.com/page"


def test_bing_engine_does_not_mangle_a_ddg_url():
    """A bing-configured provider leaves a non-bing redirect untouched."""
    provider = PlaywrightSearchProvider()
    wrapped = "https://duckduckgo.com/y.js?uddg=https%3A%2F%2Fexample.com%2Fpage"
    assert provider._resolve_url(wrapped) == wrapped


# ── Engine selection ────────────────────────────────────────────────────────


def test_engine_setting_is_honoured_not_hardcoded():
    """The setting must actually change the engine — previously it was ignored."""
    from searchops.config.settings import Settings

    s = Settings(search={"playwright_search_engine": "bing"})
    provider = PlaywrightSearchProvider(settings=s)
    assert provider.engine_name == "bing"


def test_unknown_engine_falls_back_to_bing_without_crashing():
    from searchops.config.settings import Settings

    s = Settings(search={"playwright_search_engine": "notarealengine"})
    provider = PlaywrightSearchProvider(settings=s)
    assert provider.engine_name == "bing"


# ── Search behaviour ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_query_returns_empty():
    provider = PlaywrightSearchProvider()
    assert await provider.search(SearchQuery(query="   ")) == []


@pytest.mark.asyncio
async def test_browser_failure_returns_empty_not_raise():
    """A dead browser must degrade to no results, never crash the pipeline."""
    provider = PlaywrightSearchProvider()

    # async_playwright is imported inside search(), so patch it where it lives.
    import playwright.async_api as pw_api

    with patch.object(pw_api, "async_playwright", side_effect=RuntimeError("no browser")):
        result = await provider.search(SearchQuery(query="anything"))

    assert result == []
