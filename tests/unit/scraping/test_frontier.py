"""Tests for the BFS crawl frontier.

The frontier is what turns "scrape 3 URLs" into an actual crawler for corpus
mode. The contract these tests pin:

* **Breadth-first, depth-limited.** Seeds are depth 0; links discovered on a
  page are depth+1. A URL at ``max_depth`` is never expanded.
* **The visited set is the already-built ``URLSeenSet``** — that is what it was
  designed for. A URL already seen is never enqueued twice.
* **include/exclude are regex over the full URL**, and exclude wins.
* **Same-host by default**, with an opt-in for cross-host crawling.
* **Priority order** keeps the best pages fetchable first under any cap.
* **Precision mode is untouched** — ``max_depth=1`` means no expansion, so a
  precision run behaves exactly as before.
* The frontier is pure: it never performs I/O itself, so it is testable
  without a network.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.scraping.frontier import (
    CrawlConfig,
    CrawlFrontier,
    extract_links,
    normalise_link,
)

# ── Link extraction and normalisation ──────────────────────────────────────


class TestLinkNormalisation:
    def test_relative_link_resolves_against_base(self) -> None:
        assert normalise_link("/docs/api", "https://site.com/guide") == "https://site.com/docs/api"

    def test_relative_without_leading_slash(self) -> None:
        assert normalise_link("api", "https://site.com/guide/intro") == "https://site.com/guide/api"

    def test_absolute_link_is_kept(self) -> None:
        assert normalise_link("https://other.com/x", "https://site.com/") == "https://other.com/x"

    def test_fragment_is_dropped(self) -> None:
        assert normalise_link("/a#section", "https://site.com/") == "https://site.com/a"

    def test_non_http_scheme_is_rejected(self) -> None:
        assert normalise_link("mailto:x@y.com", "https://site.com/") is None
        assert normalise_link("javascript:void(0)", "https://site.com/") is None

    def test_empty_link_is_rejected(self) -> None:
        assert normalise_link("", "https://site.com/") is None
        assert normalise_link("   ", "https://site.com/") is None


class TestExtractLinks:
    def test_extracts_hrefs(self) -> None:
        html = '<a href="/a">A</a><a href="/b">B</a>'
        assert set(extract_links(html, "https://site.com/")) == {
            "https://site.com/a",
            "https://site.com/b",
        }

    def test_extracts_from_single_quoted_href(self) -> None:
        html = "<a href='/a'>A</a>"
        assert extract_links(html, "https://site.com/") == ["https://site.com/a"]

    def test_ignores_non_http(self) -> None:
        html = '<a href="mailto:a@b.c">mail</a><a href="/ok">ok</a>'
        assert extract_links(html, "https://site.com/") == ["https://site.com/ok"]

    def test_deduplicates(self) -> None:
        html = '<a href="/a">1</a><a href="/a">2</a>'
        assert extract_links(html, "https://site.com/") == ["https://site.com/a"]

    def test_no_links_returns_empty(self) -> None:
        assert extract_links("<html><body>no links</body></html>", "https://site.com/") == []

    def test_empty_html_returns_empty(self) -> None:
        assert extract_links("", "https://site.com/") == []


# ── Crawl config ───────────────────────────────────────────────────────────


class TestCrawlConfig:
    def test_precision_mode_does_not_expand(self) -> None:
        """max_depth=1 means seeds only — the precision behaviour is preserved."""
        cfg = CrawlConfig.from_scrape_plan(None)
        assert cfg.max_depth == 1
        assert cfg.expands is False

    def test_corpus_mode_expands(self) -> None:
        cfg = CrawlConfig.from_scrape_plan({"mode": "corpus", "max_depth": 3})
        assert cfg.max_depth == 3
        assert cfg.expands is True

    def test_include_exclude_are_compiled(self) -> None:
        cfg = CrawlConfig(include_patterns=[r"/docs/"], exclude_patterns=[r"/private/"])
        assert cfg.include.search("https://site.com/docs/a") is not None
        assert cfg.exclude.search("https://site.com/private/a") is not None

    def test_missing_patterns_mean_no_filter(self) -> None:
        cfg = CrawlConfig()
        assert cfg.include is None
        assert cfg.exclude is None


# ── Frontier behaviour ─────────────────────────────────────────────────────


class _FakeSeen:
    """URLSeenSet-shaped double: ``seen()`` records and reports first-seen."""

    def __init__(self, already: set[str] | None = None) -> None:
        self._already = set(already or ())
        self.recorded: list[str] = []

    async def seen(self, url: str, *, force: bool = False) -> bool:
        self.recorded.append(url)
        if url in self._already:
            return True
        self._already.add(url)
        return False


def _frontier(seen: _FakeSeen | None = None, **cfg_kwargs: Any) -> CrawlFrontier:
    return CrawlFrontier(seen=seen or _FakeSeen(), config=CrawlConfig(**cfg_kwargs))


class TestFrontierSeeding:
    @pytest.mark.asyncio
    async def test_seeds_are_enqueued(self) -> None:
        f = _frontier()
        await f.add_seeds(["https://site.com/a", "https://site.com/b"])
        assert f.size == 2

    @pytest.mark.asyncio
    async def test_seed_already_seen_is_not_enqueued(self) -> None:
        f = _frontier(_FakeSeen(already={"https://site.com/a"}))
        await f.add_seeds(["https://site.com/a", "https://site.com/b"])
        assert f.size == 1

    @pytest.mark.asyncio
    async def test_duplicate_seed_is_enqueued_once(self) -> None:
        f = _frontier()
        await f.add_seeds(["https://site.com/a", "https://site.com/a"])
        assert f.size == 1


class TestFrontierExpansion:
    @pytest.mark.asyncio
    async def test_depth_one_seed_is_not_expanded(self) -> None:
        """Precision mode: a depth-1 seed yields no links."""
        f = _frontier(max_depth=1)
        await f.add_seeds(["https://site.com/a"])
        page = await f.next_url()
        assert page is not None
        await f.record_links(page.url, '<a href="/b">B</a>')
        assert f.size == 0

    @pytest.mark.asyncio
    async def test_depth_two_expands_once(self) -> None:
        f = _frontier(max_depth=2)
        await f.add_seeds(["https://site.com/a"])
        page = await f.next_url()
        assert page is not None
        assert page.depth == 0
        await f.record_links(page.url, '<a href="/b">B</a>')
        assert f.size == 1
        child = await f.next_url()
        assert child is not None
        assert child.depth == 1

    @pytest.mark.asyncio
    async def test_max_depth_is_respected(self) -> None:
        """Pages beyond ``max_depth`` are fetched but never expanded.

        With ``max_depth=2`` depths 0, 1 and 2 are fetched. A depth-2 page's
        links would be depth 3, which exceeds the limit, so they are dropped.
        """
        f = _frontier(max_depth=2)
        await f.add_seeds(["https://site.com/a"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="/b">B</a>')
        p2 = await f.next_url()
        assert p2 is not None
        assert p2.depth == 1

        # /b is depth 1, so /c lands at depth 2 — still within the limit.
        assert await f.record_links(p2.url, '<a href="/c">C</a>') == 1
        p3 = await f.next_url()
        assert p3 is not None
        assert p3.depth == 2

        # /c is at max_depth, so its links (depth 3) must NOT be enqueued.
        assert await f.record_links(p3.url, '<a href="/d">D</a>') == 0
        assert f.size == 0

    @pytest.mark.asyncio
    async def test_visited_url_is_not_re_enqueued(self) -> None:
        f = _frontier(max_depth=3)
        await f.add_seeds(["https://site.com/a", "https://site.com/b"])
        p1 = await f.next_url()
        # /a links back to /b, which is already queued.
        await f.record_links(p1.url, '<a href="/b">B</a>')
        assert f.size == 1

    @pytest.mark.asyncio
    async def test_already_seen_url_is_not_enqueued(self) -> None:
        seen = _FakeSeen(already={"https://site.com/c"})
        f = _frontier(seen, max_depth=2)
        await f.add_seeds(["https://site.com/a"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="/c">C</a><a href="/d">D</a>')
        urls = {u.url for u in await f.drain()}
        assert "https://site.com/c" not in urls
        assert "https://site.com/d" in urls


class TestFrontierScope:
    @pytest.mark.asyncio
    async def test_same_host_by_default(self) -> None:
        f = _frontier(max_depth=2)
        await f.add_seeds(["https://site.com/a"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="https://other.com/x">X</a><a href="/y">Y</a>')
        urls = {u.url for u in await f.drain()}
        assert urls == {"https://site.com/y"}

    @pytest.mark.asyncio
    async def test_cross_host_when_allowed(self) -> None:
        f = _frontier(max_depth=2, same_host=False)
        await f.add_seeds(["https://site.com/a"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="https://other.com/x">X</a>')
        urls = {u.url for u in await f.drain()}
        assert urls == {"https://other.com/x"}

    @pytest.mark.asyncio
    async def test_include_pattern_filters(self) -> None:
        f = _frontier(max_depth=2, include_patterns=[r"/docs/"])
        await f.add_seeds(["https://site.com/start"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="/docs/a">A</a><a href="/blog/b">B</a>')
        urls = {u.url for u in await f.drain()}
        assert urls == {"https://site.com/docs/a"}

    @pytest.mark.asyncio
    async def test_exclude_beats_include(self) -> None:
        f = _frontier(max_depth=2, include_patterns=[r"/docs/"], exclude_patterns=[r"/docs/private/"])
        await f.add_seeds(["https://site.com/start"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="/docs/a">A</a><a href="/docs/private/b">B</a>')
        urls = {u.url for u in await f.drain()}
        assert urls == {"https://site.com/docs/a"}

    @pytest.mark.asyncio
    async def test_seed_is_not_filtered(self) -> None:
        """A seed the caller chose is always crawled, regardless of patterns."""
        f = _frontier(max_depth=2, include_patterns=[r"/docs/"])
        await f.add_seeds(["https://site.com/landing"])
        p1 = await f.next_url()
        assert p1 is not None
        assert p1.url == "https://site.com/landing"


class TestFrontierBudget:
    @pytest.mark.asyncio
    async def test_max_pages_caps_the_crawl(self) -> None:
        f = _frontier(max_pages=3, max_depth=5)
        await f.add_seeds([f"https://site.com/s{i}" for i in range(10)])
        count = 0
        while await f.next_url() is not None:
            count += 1
        assert count == 3

    @pytest.mark.asyncio
    async def test_empty_frontier_returns_none(self) -> None:
        f = _frontier()
        assert await f.next_url() is None

    @pytest.mark.asyncio
    async def test_stats_track_depth_and_scope(self) -> None:
        f = _frontier(max_depth=3)
        await f.add_seeds(["https://site.com/a"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="/b">B</a><a href="https://x.com/c">C</a>')
        await f.drain()
        stats = f.stats_dict()
        assert stats["links_seen"] == 2
        # Both the seed and the discovered link were enqueued.
        assert stats["links_enqueued"] == 2
        assert stats["out_of_scope"] == 1
        assert stats["pages_fetched"] == 2


class TestFrontierPriority:
    @pytest.mark.asyncio
    async def test_shallower_pages_are_fetched_first(self) -> None:
        """BFS order: every depth-0 page precedes any depth-1 page."""
        f = _frontier(max_depth=3)
        await f.add_seeds(["https://site.com/a", "https://site.com/b"])
        p1 = await f.next_url()
        await f.record_links(p1.url, '<a href="/deep">D</a>')
        order = [p1.url]
        while (page := await f.next_url()) is not None:
            order.append(page.url)
        assert order.index("https://site.com/b") < order.index("https://site.com/deep")
