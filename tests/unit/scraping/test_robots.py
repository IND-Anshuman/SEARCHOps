"""Tests for robots.txt parsing, Crawl-Delay application, and the robots gate.

The gate is the correctness layer that was missing entirely: before this,
SEARCHOps fetched arbitrary URLs with no awareness of robots.txt. The design
decisions these tests pin:

* Disallow wins over Allow at the same specificity (the RFC 9309 rule), and a
  longer match wins over a shorter one.
* A malformed or unreachable robots.txt must NOT block a scrape (fail-open).
  The legal risk is in *ignoring* robots, not in a parse error.
* Crawl-Delay becomes a per-domain rate limit — no new rate path.
"""

from __future__ import annotations

import pytest

from searchops.scraping.robots import (
    RobotsCache,
    parse_robots_txt,
    robots_crawl_delay,
    robots_url_allowed,
)

# ── Parsing ─────────────────────────────────────────────────────────────────


class TestParseRobotsTxt:
    def test_parses_user_agent_group(self) -> None:
        txt = """
User-agent: *
Disallow: /admin
Disallow: /private/
Allow: /admin/public
"""
        rules = parse_robots_txt(txt)
        assert "/admin" in rules.disallow
        assert "/private/" in rules.disallow
        assert "/admin/public" in rules.allow

    def test_user_agent_group_is_case_insensitive(self) -> None:
        txt = "User-agent: MyBot\nDisallow: /x\n"
        # Agent names are matched case-insensitively.
        assert parse_robots_txt(txt, user_agent="mybot").disallow == ("/x",)
        assert parse_robots_txt(txt, user_agent="MYBOT").disallow == ("/x",)

    def test_unmatched_agent_falls_back_to_permissive(self) -> None:
        """A group naming a different agent does not apply to us."""
        txt = "User-agent: MyBot\nDisallow: /x\n"
        assert parse_robots_txt(txt, user_agent="OtherBot").disallow == ()

    def test_agent_specific_group_is_separate_from_wildcard(self) -> None:
        txt = """
User-agent: *
Disallow: /public

User-agent: MyBot
Disallow: /secret
"""
        mine = parse_robots_txt(txt, user_agent="MyBot")
        assert mine.disallow == ("/secret",)

        wildcard = parse_robots_txt(txt, user_agent="OtherBot")
        assert wildcard.disallow == ("/public",)

    def test_group_ends_at_next_user_agent(self) -> None:
        txt = """
User-agent: MyBot
Disallow: /a

User-agent: OtherBot
Disallow: /b
"""
        rules = parse_robots_txt(txt, user_agent="MyBot")
        assert "/a" in rules.disallow
        assert "/b" not in rules.disallow

    def test_comments_are_ignored(self) -> None:
        txt = "User-agent: *  # our crawler\nDisallow: /c  # block this\n"
        rules = parse_robots_txt(txt)
        assert rules.disallow == ("/c",)

    def test_empty_disallow_means_allow_all(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow:\n")
        assert rules.disallow == ()

    def test_garbage_input_returns_permissive_rules(self) -> None:
        """Fail-open: garbage must not produce a blocking rule set."""
        rules = parse_robots_txt("\x00\x01 not really robots ###")
        assert rules.disallow == ()

    def test_empty_input_returns_permissive_rules(self) -> None:
        rules = parse_robots_txt("")
        assert rules.disallow == ()

    def test_missing_group_returns_permissive_rules(self) -> None:
        rules = parse_robots_txt("Sitemap: https://x/sitemap.xml\n")
        assert rules.disallow == ()


# ── Allow/Disallow matching (RFC 9309 longest-match precedence) ─────────────


class TestUrlAllowed:
    def test_root_disallow_blocks_everything(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /\n")
        assert robots_url_allowed("https://site.com/", rules) is False
        assert robots_url_allowed("https://site.com/a/b", rules) is False

    def test_disallow_path_prefix(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /admin\n")
        assert robots_url_allowed("https://site.com/admin", rules) is False
        assert robots_url_allowed("https://site.com/admin/x", rules) is False
        assert robots_url_allowed("https://site.com/administrator", rules) is False

    def test_allow_permits_otherwise_allowed_path(self) -> None:
        rules = parse_robots_txt("User-agent: *\nAllow: /a\n")
        assert robots_url_allowed("https://site.com/a/b", rules) is True

    def test_longer_allow_wins_over_shorter_disallow(self) -> None:
        """RFC 9309: the most specific (longest) matching rule wins."""
        txt = """
User-agent: *
Disallow: /admin
Allow: /admin/public
"""
        rules = parse_robots_txt(txt)
        assert robots_url_allowed("https://site.com/admin/public/x", rules) is True
        assert robots_url_allowed("https://site.com/admin/private", rules) is False

    def test_allow_takes_precedence_at_equal_length(self) -> None:
        """Equal specificity: allow wins (the permissive tie-break)."""
        txt = "User-agent: *\nDisallow: /abc\nAllow: /abc\n"
        rules = parse_robots_txt(txt)
        assert robots_url_allowed("https://site.com/abc", rules) is True

    def test_query_string_is_part_of_path(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /*?print=1\n")
        assert robots_url_allowed("https://site.com/p?print=1", rules) is False
        assert robots_url_allowed("https://site.com/p", rules) is True

    def test_wildcard_star_matches_any_sequence(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /*.pdf$\n")
        assert robots_url_allowed("https://site.com/doc.pdf", rules) is False
        assert robots_url_allowed("https://site.com/doc.html", rules) is True

    def test_unparseable_url_is_allowed(self) -> None:
        """Fail-open: a URL we cannot parse must not be treated as blocked."""
        rules = parse_robots_txt("User-agent: *\nDisallow: /\n")
        assert robots_url_allowed("", rules) is True


# ── Crawl-Delay ─────────────────────────────────────────────────────────────


class TestCrawlDelay:
    def test_parses_integer_delay(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /a\nCrawl-delay: 10\n")
        assert robots_crawl_delay(rules) == 10.0

    def test_parses_fractional_delay(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /a\nCrawl-delay: 0.5\n")
        assert robots_crawl_delay(rules) == 0.5

    def test_absent_delay_is_none(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /a\n")
        assert robots_crawl_delay(rules) is None

    def test_non_numeric_delay_is_ignored(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /a\nCrawl-delay: soon\n")
        assert robots_crawl_delay(rules) is None

    def test_negative_delay_is_ignored(self) -> None:
        rules = parse_robots_txt("User-agent: *\nDisallow: /a\nCrawl-delay: -5\n")
        assert robots_crawl_delay(rules) is None

    def test_delay_only_applies_within_own_group(self) -> None:
        txt = """
User-agent: *
Disallow: /a
Crawl-delay: 10

User-agent: MyBot
Disallow: /b
"""
        assert robots_crawl_delay(parse_robots_txt(txt, user_agent="MyBot")) is None


# ── Cache ───────────────────────────────────────────────────────────────────


class TestRobotsCache:
    @pytest.mark.asyncio
    async def test_parses_robots_url_from_base(self) -> None:
        cache = RobotsCache(fetcher=self._fetcher({"https://site.com/robots.txt": "User-agent: *\nDisallow: /x\n"}))
        rules = await cache.get("https://site.com/a/b?q=1")
        assert rules.disallow == ("/x",)

    @pytest.mark.asyncio
    async def test_404_means_no_rules(self) -> None:
        async def fetcher(url: str) -> str | None:
            if url.endswith("robots.txt"):
                return None
            raise AssertionError("only robots.txt should be fetched")

        cache = RobotsCache(fetcher=fetcher)
        rules = await cache.get("https://site.com/a")
        assert rules.disallow == ()

    @pytest.mark.asyncio
    async def test_second_request_hits_cache(self) -> None:
        calls: list[str] = []

        async def fetcher(url: str) -> str | None:
            calls.append(url)
            return "User-agent: *\nDisallow: /x\n"

        cache = RobotsCache(fetcher=fetcher)
        await cache.get("https://site.com/a")
        await cache.get("https://site.com/b")
        assert calls == ["https://site.com/robots.txt"]

    @pytest.mark.asyncio
    async def test_fetch_error_fails_open(self) -> None:
        async def fetcher(url: str) -> str | None:
            raise ConnectionError("dns failure")

        cache = RobotsCache(fetcher=fetcher)
        rules = await cache.get("https://site.com/a")
        assert rules.disallow == ()
        assert cache.stats.errors == 1

    @pytest.mark.asyncio
    async def test_error_is_not_cached_so_a_retry_can_succeed(self) -> None:
        state = {"fail": True}

        async def fetcher(url: str) -> str | None:
            if state["fail"]:
                state["fail"] = False
                raise ConnectionError("temporary")
            return "User-agent: *\nDisallow: /y\n"

        cache = RobotsCache(fetcher=fetcher)
        assert (await cache.get("https://site.com/a")).disallow == ()
        assert (await cache.get("https://site.com/b")).disallow == ("/y",)

    @staticmethod
    def _fetcher(bodies: dict[str, str | None]):
        async def fetcher(url: str) -> str | None:
            return bodies.get(url)

        return fetcher
