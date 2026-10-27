"""TOOL_PROFILE=api: Tavily for search and page text, no free tools in the path.

The slim deployment image leaves out the browser, Crawl4AI, SearXNG and the
other local tools. These tests pin that the API profile never reaches for
them: page text comes from Tavily Extract, key-free search providers are not
routed to, and the PDF export says plainly why it is unavailable.
"""

from __future__ import annotations

import functools
from typing import Any

import httpx
import pytest
import respx

from searchops.config.settings import get_settings
from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest
from searchops.scraping.tavily_extract import EXTRACT_URL, TavilyExtractPipeline

pytestmark = pytest.mark.unit


@pytest.fixture
def api_profile(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("TOOL_PROFILE", "api")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        self.store[key] = value
        return True


@pytest.mark.asyncio
@respx.mock
async def test_page_text_comes_from_tavily_extract_and_is_cached(api_profile: Any) -> None:
    route = respx.post(EXTRACT_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"url": "https://a.example/p", "raw_content": "# Qdrant\n\nFast [link](/x)."}]}
        )
    )
    cache = _Cache()
    fetcher = TavilyExtractPipeline(cache=cache)  # type: ignore[arg-type]

    first = await fetcher.execute(ScrapeRequest(url="https://a.example/p"))
    assert first.status_code == 200 and first.markdown is not None and first.markdown.startswith("# Qdrant")
    assert first.scrape_mode_used is ScrapeMode.TAVILY_EXTRACT
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer tvly-test-key"
    assert b'"extract_depth":"basic"' in sent.content.replace(b" ", b"")

    again = await fetcher.execute(ScrapeRequest(url="https://a.example/p"))
    assert again.was_cached and route.call_count == 1  # a repeat costs no credit
    await fetcher.close()


@pytest.mark.asyncio
@respx.mock
async def test_failures_are_results_not_exceptions(api_profile: Any) -> None:
    respx.post(EXTRACT_URL).mock(
        side_effect=[
            httpx.Response(200, json={"results": [], "failed_results": [{"url": "u", "error": "blocked"}]}),
            httpx.Response(432, text="plan limit"),
        ]
    )
    fetcher = TavilyExtractPipeline()
    blocked = await fetcher.execute(ScrapeRequest(url="https://b.example"))
    assert blocked.status_code != 200 and blocked.metadata["error"] == "blocked"
    limited = await fetcher.execute(ScrapeRequest(url="https://c.example"))
    assert limited.status_code == 432
    await fetcher.close()


@pytest.mark.asyncio
async def test_missing_key_fails_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    result = await TavilyExtractPipeline().execute(ScrapeRequest(url="https://a.example"))
    get_settings.cache_clear()
    assert result.status_code == 401


def test_the_factory_picks_the_fetcher_for_the_profile(api_profile: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from searchops.scraping.factory import build_scraping_pipeline

    assert isinstance(build_scraping_pipeline(), TavilyExtractPipeline)
    monkeypatch.setenv("TOOL_PROFILE", "local")
    get_settings.cache_clear()
    from searchops.scraping.pipeline import ScrapingPipeline

    assert isinstance(build_scraping_pipeline(), ScrapingPipeline)


@pytest.mark.parametrize(("profile", "expect_key_free"), [("api", False), ("local", True)])
def test_key_free_search_runs_only_in_the_local_profile(
    monkeypatch: pytest.MonkeyPatch, profile: str, expect_key_free: bool
) -> None:
    from searchops.search.registry import SearchProviderRegistry

    monkeypatch.setenv("TOOL_PROFILE", profile)
    get_settings.cache_clear()
    registry = SearchProviderRegistry(auto_discover=True)
    names = {p.name for p in registry.list_providers()}
    get_settings.cache_clear()
    assert "tavily" in names
    assert bool({"searxng", "playwright"} & names) is expect_key_free
    assert bool(registry.key_free_providers()) is expect_key_free


@pytest.mark.asyncio
async def test_pdf_export_explains_itself_without_a_browser(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import builtins

    from searchops.application.exports.pdf_export import ExportUnavailableError, export_pdf
    from searchops.application.report_document import ReportDocument

    real_import = builtins.__import__

    def _no_playwright(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("playwright"):
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", functools.partial(_no_playwright))
    doc = ReportDocument.from_markdown("j", "q", "# Title\n\nBody.")
    with pytest.raises(ExportUnavailableError, match="Word"):
        await export_pdf(doc, out_dir=tmp_path)


def test_the_global_registry_discovers_providers_at_import() -> None:
    """The module builds its singleton as it is imported, so every helper discovery
    calls must be defined above that line. A helper placed below it raised NameError
    inside discovery, was swallowed by its broad except, and left the real registry
    empty: no search in production. A registry the test builds itself cannot show that."""
    import subprocess
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import os, sys; sys.path.insert(0, 'src'); os.environ['TOOL_PROFILE'] = 'api'; os.environ['TAVILY_API_KEY'] = 'x';"
            "from searchops.search.registry import registry; print(sorted(p.name for p in registry.list_providers()))",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    enabled = out.strip().splitlines()[-1]  # the printed list; earlier lines are log output
    assert "tavily" in enabled and "serper" in enabled, out
    assert "searxng" not in enabled and "playwright" not in enabled, out  # key-free tools are off in the API profile


@pytest.mark.parametrize("profile", ["api", "local"])
def test_the_pipelines_deep_search_reaches_tavily(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """The research pipeline searches with SearchProfile.DEEP. Tavily once lacked the KEYWORD
    capability that profile requires, so it matched nothing: with the key-free tools off
    (TOOL_PROFILE=api) the only candidate was Bright Data, which has no key, and a run
    finished in seconds with "No sources were retrieved"."""
    from searchops.search.contracts import SearchQuery
    from searchops.search.domain.models import SearchProfile
    from searchops.search.policy import policy_engine
    from searchops.search.registry import SearchProviderRegistry

    monkeypatch.setenv("TOOL_PROFILE", profile)
    get_settings.cache_clear()
    registry = SearchProviderRegistry(auto_discover=True)
    needed = policy_engine.apply_profile(SearchQuery(query="q", profile=SearchProfile.DEEP))
    order = [p.name for p in registry.resolve_by_capabilities(needed)]
    get_settings.cache_clear()
    assert "tavily" in order, order
    if profile == "api":
        assert order[0] == "tavily", order  # the primary provider, ahead of the unkeyed Bright Data
