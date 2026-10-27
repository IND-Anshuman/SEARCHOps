"""Tests for the robots gate inside ScrapingPipeline.execute().

The gate must behave correctly in exactly two situations, and they are the
whole point:

* A URL robots.txt forbids is NOT fetched by any tier. It is recorded as
  ``skipped_robots`` — an intentional skip, not a failure — so it never
  reaches the DLQ or counts against a circuit breaker.
* When robots.txt cannot be determined (fetch error), the fetch proceeds.
  Blocking on our own inability to read robots would be a false restriction
  and would break every domain whose robots.txt is unreachable.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.scraping.pipeline import ScrapingPipeline
from searchops.scraping.rate_limiter import DomainRateLimiter
from searchops.scraping.robots import RobotsCache


class _FakeStealth:
    """Records whether it was asked to fetch, and returns a fixed result."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def scrape(self, request: Any) -> Any:
        from searchops.core.interfaces.scraper import ScrapeMode, ScrapeResult

        self.calls.append(request.url)
        return ScrapeResult(
            url=request.url,
            final_url=request.url,
            status_code=200,
            markdown="content",
            scrape_mode_used=ScrapeMode.HTTP,
        )

    async def health_check(self) -> bool:
        return True


def _robots_cache(body: str | None, *, error: bool = False) -> RobotsCache:
    async def fetcher(url: str) -> str | None:
        if error:
            raise ConnectionError("unreachable")
        return body

    return RobotsCache(fetcher=fetcher)


def _pipeline(stealth: _FakeStealth, robots: RobotsCache) -> ScrapingPipeline:
    return ScrapingPipeline(stealth=stealth, robots_cache=robots, rate_limiter=DomainRateLimiter())  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolated_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the process-wide rate-limiter singleton.

    ``get_rate_limiter()`` is module-level shared state. Tests that fetch the
    same host repeatedly would otherwise exhaust its per-domain budget and
    rate-limit *unrelated* tests that run later in the same session.
    """
    from searchops.scraping import rate_limiter as rl_mod

    monkeypatch.setattr(rl_mod, "_RATE_LIMITER", rl_mod.DomainRateLimiter(), raising=False)


class TestRobotsGate:
    @pytest.mark.asyncio
    async def test_allowed_url_is_fetched(self) -> None:
        stealth = _FakeStealth()
        robots = _robots_cache("User-agent: *\nDisallow: /private\n")
        await _pipeline(stealth, robots).execute(_request("https://site.com/public/page"))
        assert stealth.calls == ["https://site.com/public/page"]

    @pytest.mark.asyncio
    async def test_disallowed_url_is_never_fetched(self) -> None:
        stealth = _FakeStealth()
        robots = _robots_cache("User-agent: *\nDisallow: /private\n")
        result = await _pipeline(stealth, robots).execute(_request("https://site.com/private/secret"))
        # No tier ran at all.
        assert stealth.calls == []
        assert result.status_code != 200
        assert result.metadata.get("skipped") == "robots"

    @pytest.mark.asyncio
    async def test_disallowed_is_not_a_failure(self) -> None:
        """A robots skip is intentional, so it must not look like an error."""
        stealth = _FakeStealth()
        robots = _robots_cache("User-agent: *\nDisallow: /\n")
        result = await _pipeline(stealth, robots).execute(_request("https://site.com/a"))
        assert result.status_code == 403
        assert "robots" in result.metadata.get("reason", "").lower()
        assert "error" not in result.metadata

    @pytest.mark.asyncio
    async def test_robots_fetch_error_fails_open(self) -> None:
        """Unreachable robots.txt must not block the fetch."""
        stealth = _FakeStealth()
        robots = _robots_cache(None, error=True)
        await _pipeline(stealth, robots).execute(_request("https://site.com/anything"))
        assert stealth.calls == ["https://site.com/anything"]

    @pytest.mark.asyncio
    async def test_missing_robots_file_allows_fetch(self) -> None:
        stealth = _FakeStealth()
        robots = _robots_cache(None)  # 404 → no rules
        await _pipeline(stealth, robots).execute(_request("https://site.com/x"))
        assert stealth.calls == ["https://site.com/x"]

    @pytest.mark.asyncio
    async def test_robots_are_fetched_once_per_host(self) -> None:
        fetched: list[str] = []

        async def fetcher(url: str) -> str | None:
            fetched.append(url)
            return "User-agent: *\nDisallow: /p\n"

        robots = RobotsCache(fetcher=fetcher)
        pipeline = _pipeline(_FakeStealth(), robots)
        for path in ("/a", "/b", "/c"):
            await pipeline.execute(_request(f"https://site.com{path}"))
        assert fetched == ["https://site.com/robots.txt"]


def _request(url: str) -> Any:
    from searchops.core.interfaces.scraper import ScrapeRequest

    return ScrapeRequest(url=url)
