"""
Unit tests for searchops.scraping.crawl4ai_engine (Phase 3).

All crawl4ai / browser calls are mocked — no network traffic occurs.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.crawl4ai_engine import (
    Crawl4AIConfig,
    Crawl4AIScraper,
    _DEFAULT_MAX_CONCURRENCY,
    _ENTROPY_THRESHOLD,
    _WORD_COUNT_MIN,
    build_crawl4ai_scraper,
    close_crawl4ai_scraper,
    get_crawl4ai_scraper,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_crawl_result(
    *,
    success: bool = True,
    fit_markdown: str | None = "# Fit Content\n\nRelevant text.",
    raw_markdown: str | None = "# Raw\n\nAll text.",
    html: str | None = "<html><body><h1>Test</h1></body></html>",
    url: str = "https://example.com",
    links: dict | None = None,
    metadata: dict | None = None,
    error_message: str | None = None,
) -> MagicMock:
    result = MagicMock()
    result.success = success
    result.url = url
    result.html = html
    result.error_message = error_message

    if fit_markdown is not None:
        fm = MagicMock()
        fm.raw_markdown = fit_markdown
        result.fit_markdown = fm
    else:
        result.fit_markdown = None

    if raw_markdown is not None:
        rm = MagicMock()
        rm.raw_markdown = raw_markdown
        result.markdown = rm
    else:
        result.markdown = None

    result.links = links if links is not None else {"internal": [], "external": []}
    result.metadata = metadata if metadata is not None else {"title": "Test Page"}
    return result


def _make_mock_crawler(crawl_result: MagicMock) -> AsyncMock:
    crawler = AsyncMock()
    crawler.__aenter__ = AsyncMock(return_value=crawler)
    crawler.__aexit__ = AsyncMock(return_value=None)
    crawler.arun = AsyncMock(return_value=crawl_result)
    return crawler


def _patches():
    """Context managers that suppress all crawl4ai internals."""
    return [
        patch("searchops.scraping.crawl4ai_engine.BrowserConfig"),
        patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"),
        patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"),
        patch("searchops.scraping.crawl4ai_engine.CacheMode"),
    ]


# ---------------------------------------------------------------------------
# TestCrawl4AIConfig
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrawl4AIConfig:

    def test_defaults_are_valid(self) -> None:
        cfg = Crawl4AIConfig()
        assert cfg.headless is True
        assert cfg.entropy_threshold == _ENTROPY_THRESHOLD
        assert cfg.word_count_min == _WORD_COUNT_MIN
        assert cfg.exclude_external_links is True
        assert cfg.remove_overlay_elements is True
        assert cfg.use_fit_markdown is True
        assert isinstance(cfg.extra_browser_args, list)
        assert len(cfg.extra_browser_args) >= 1

    def test_custom_values_accepted(self) -> None:
        cfg = Crawl4AIConfig(headless=False, entropy_threshold=0.6, word_count_min=20)
        assert cfg.headless is False
        assert cfg.entropy_threshold == 0.6
        assert cfg.word_count_min == 20

    def test_frozen_prevents_mutation(self) -> None:
        cfg = Crawl4AIConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.headless = False  # type: ignore[misc]

    def test_extra_browser_args_independent_instances(self) -> None:
        cfg1 = Crawl4AIConfig()
        cfg2 = Crawl4AIConfig()
        assert cfg1.extra_browser_args is not cfg2.extra_browser_args


# ---------------------------------------------------------------------------
# TestLifecycle
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrawl4AIScraperLifecycle:

    @pytest.mark.asyncio
    async def test_crawler_none_before_first_use(self) -> None:
        assert Crawl4AIScraper()._crawler is None

    @pytest.mark.asyncio
    async def test_start_initialises_crawler(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result())

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            for p in _patches():
                p.start()
            await scraper.start()
            for p in _patches():
                p.stop()

        assert scraper._crawler is not None

    @pytest.mark.asyncio
    async def test_close_sets_crawler_to_none(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result())

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            for p in _patches():
                p.start()
            await scraper.start()
            assert scraper._crawler is not None
            await scraper.close()
            assert scraper._crawler is None
            for p in _patches():
                p.stop()

    @pytest.mark.asyncio
    async def test_close_when_not_started_is_noop(self) -> None:
        await Crawl4AIScraper().close()

    @pytest.mark.asyncio
    async def test_concurrent_start_creates_only_one_crawler(self) -> None:
        scraper = Crawl4AIScraper()
        call_count = 0

        async def _fake_create(self_inner):
            nonlocal call_count
            call_count += 1
            return _make_mock_crawler(_make_crawl_result())

        with patch.object(Crawl4AIScraper, "_create_crawler", _fake_create):
            await asyncio.gather(scraper.start(), scraper.start(), scraper.start())

        assert call_count == 1


# ---------------------------------------------------------------------------
# TestScrape
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrawl4AIScraperScrape:

    @pytest.mark.asyncio
    async def test_success_returns_200(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(fit_markdown="# Hello"))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.status_code == 200
        assert result.scrape_mode_used == ScrapeMode.CRAWL4AI

    @pytest.mark.asyncio
    async def test_success_uses_fit_markdown(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(fit_markdown="# Fit", raw_markdown="# Raw"))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.markdown == "# Fit"

    @pytest.mark.asyncio
    async def test_falls_back_to_raw_markdown(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(fit_markdown=None, raw_markdown="# Raw Only"))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.markdown == "# Raw Only"

    @pytest.mark.asyncio
    async def test_no_markdown_gives_empty_string(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(fit_markdown=None, raw_markdown=None))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.markdown == ""
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_failure_returns_500(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(success=False, error_message="Network error"))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.status_code == 500
        assert "error" in result.metadata

    @pytest.mark.asyncio
    async def test_exception_in_crawl_returns_500(self) -> None:
        scraper = Crawl4AIScraper()
        with patch.object(scraper, "_crawl", side_effect=RuntimeError("boom")):
            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))
        assert result.status_code == 500
        assert "boom" in result.metadata.get("error", "")

    @pytest.mark.asyncio
    async def test_final_url_set_from_result(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(url="https://example.com/final"))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_metadata_contains_fit_markdown_len(self) -> None:
        content = "word " * 100
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(fit_markdown=content))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.metadata["fit_markdown_len"] == len(content)

    @pytest.mark.asyncio
    async def test_word_count_is_populated(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(fit_markdown="one two three"))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.word_count == 3

    @pytest.mark.asyncio
    async def test_duration_ms_is_non_negative(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result())

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.duration_ms is not None and result.duration_ms >= 0


# ---------------------------------------------------------------------------
# TestLinkExtraction
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrawl4AILinkExtraction:

    @pytest.mark.asyncio
    async def test_dict_links_extracted_as_hrefs(self) -> None:
        scraper = Crawl4AIScraper()
        links = {"internal": [{"href": "/a"}, {"href": "/b"}], "external": []}
        mock_crawler = _make_mock_crawler(_make_crawl_result(links=links))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.links == ["/a", "/b"]

    @pytest.mark.asyncio
    async def test_links_capped_at_50(self) -> None:
        scraper = Crawl4AIScraper()
        many = [{"href": f"/p{i}"} for i in range(100)]
        mock_crawler = _make_mock_crawler(_make_crawl_result(links={"internal": many}))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert len(result.links) == 50

    @pytest.mark.asyncio
    async def test_string_links_normalised(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(links={"internal": ["/x", "/y"]}))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.links == ["/x", "/y"]

    @pytest.mark.asyncio
    async def test_no_links_returns_empty_list(self) -> None:
        scraper = Crawl4AIScraper()
        mock_crawler = _make_mock_crawler(_make_crawl_result(links={}))

        with patch("searchops.scraping.crawl4ai_engine.AsyncWebCrawler", return_value=mock_crawler):
            with patch("searchops.scraping.crawl4ai_engine.BrowserConfig"):
                with patch("searchops.scraping.crawl4ai_engine.PruningContentFilter"):
                    with patch("searchops.scraping.crawl4ai_engine.CrawlerRunConfig"):
                        with patch("searchops.scraping.crawl4ai_engine.CacheMode"):
                            result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

        assert result.links == []


# ---------------------------------------------------------------------------
# TestScrapeMany
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrawl4AIScraperScrapeMany:

    @pytest.mark.asyncio
    async def test_returns_correct_count(self) -> None:
        scraper = Crawl4AIScraper()
        ok = ScrapeResult(url="u", final_url="u", status_code=200, scrape_mode_used=ScrapeMode.CRAWL4AI)
        with patch.object(scraper, "scrape", AsyncMock(return_value=ok)):
            results = await scraper.scrape_many(
                [ScrapeRequest(url=f"https://x.com/{i}") for i in range(6)]
            )
        assert len(results) == 6

    @pytest.mark.asyncio
    async def test_concurrency_bounded(self) -> None:
        scraper = Crawl4AIScraper()
        peak, current = 0, 0
        limit = 3

        async def _count(req):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.01)
            current -= 1
            return ScrapeResult(url=req.url, final_url=req.url, status_code=200, scrape_mode_used=ScrapeMode.CRAWL4AI)

        with patch.object(scraper, "scrape", side_effect=_count):
            await scraper.scrape_many(
                [ScrapeRequest(url=f"https://x.com/{i}") for i in range(9)],
                max_concurrency=limit,
            )

        assert peak <= limit


# ---------------------------------------------------------------------------
# TestHealthCheck
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrawl4AIHealthCheck:

    @pytest.mark.asyncio
    async def test_returns_true_when_crawl4ai_importable(self) -> None:
        assert await Crawl4AIScraper().health_check() is True

    @pytest.mark.asyncio
    async def test_returns_false_when_crawl4ai_missing(self) -> None:
        with patch.dict(sys.modules, {"crawl4ai": None}):
            assert await Crawl4AIScraper().health_check() is False


# ---------------------------------------------------------------------------
# TestFactory
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildCrawl4AIScraper:

    def test_returns_scraper_instance(self) -> None:
        assert isinstance(build_crawl4ai_scraper(), Crawl4AIScraper)

    def test_default_config_when_none(self) -> None:
        assert build_crawl4ai_scraper()._cfg == Crawl4AIConfig()

    def test_custom_config_accepted(self) -> None:
        cfg = Crawl4AIConfig(entropy_threshold=0.7)
        assert build_crawl4ai_scraper(config=cfg)._cfg.entropy_threshold == 0.7

    def test_each_call_gives_fresh_instance(self) -> None:
        assert build_crawl4ai_scraper() is not build_crawl4ai_scraper()


# ---------------------------------------------------------------------------
# TestSingletons
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSingletonHelpers:

    @pytest.mark.asyncio
    async def test_get_returns_same_instance(self) -> None:
        import searchops.scraping.crawl4ai_engine as mod
        mod._shared_scraper = None
        with patch.object(Crawl4AIScraper, "start", AsyncMock()):
            s1 = await get_crawl4ai_scraper()
            s2 = await get_crawl4ai_scraper()
        assert s1 is s2
        mod._shared_scraper = None

    @pytest.mark.asyncio
    async def test_close_resets_singleton(self) -> None:
        import searchops.scraping.crawl4ai_engine as mod
        mod._shared_scraper = None
        with patch.object(Crawl4AIScraper, "start", AsyncMock()):
            await get_crawl4ai_scraper()
        with patch.object(Crawl4AIScraper, "close", AsyncMock()) as m:
            await close_crawl4ai_scraper()
            m.assert_called_once()
        assert mod._shared_scraper is None


# ---------------------------------------------------------------------------
# TestPipelineIntegration
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestScrapingPipelineCrawl4AITier:
    """Verify Crawl4AI is Tier 0.5 in the pipeline execution order."""

    def _make_pipeline(self, *, stealth_ok=False, crawl4ai_ok=True, playwright_ok=True):
        from unittest.mock import MagicMock
        from searchops.scraping.pipeline import ScrapingPipeline

        def _r(status, mode):
            return ScrapeResult(
                url="https://example.com", final_url="https://example.com",
                status_code=status, html="<h1>ok</h1>" if status == 200 else None,
                scrape_mode_used=mode,
            )

        stealth = AsyncMock()
        stealth._cfg = MagicMock()
        stealth._cfg.impersonate = "chrome124"
        stealth.scrape.return_value = _r(200 if stealth_ok else 500, ScrapeMode.STEALTH_HTTP)

        c4ai = AsyncMock()
        c4ai.scrape.return_value = _r(200 if crawl4ai_ok else 500, ScrapeMode.CRAWL4AI)

        pw = AsyncMock()
        pw.pool = MagicMock(stats={})
        pw.scrape.return_value = _r(200 if playwright_ok else 500, ScrapeMode.PLAYWRIGHT)

        fc = AsyncMock()
        fc.scrape.return_value = _r(200, ScrapeMode.FIRECRAWL)

        pipeline = ScrapingPipeline(stealth=stealth, proxy_router=None, crawl4ai=c4ai, firecrawl=fc, playwright=pw)
        return pipeline, stealth, c4ai, pw, fc

    @pytest.mark.asyncio
    async def test_crawl4ai_called_when_stealth_fails(self) -> None:
        pipeline, stealth, c4ai, pw, fc = self._make_pipeline(stealth_ok=False, crawl4ai_ok=True)
        req = ScrapeRequest(url="https://example.com")
        result = await pipeline.execute(req)
        assert result.status_code == 200
        assert result.scrape_mode_used == ScrapeMode.CRAWL4AI
        c4ai.scrape.assert_called_once()
        pw.scrape.assert_not_called()

    @pytest.mark.asyncio
    async def test_stealth_success_skips_crawl4ai(self) -> None:
        pipeline, stealth, c4ai, pw, fc = self._make_pipeline(stealth_ok=True)
        stealth.scrape.return_value = ScrapeResult(
            url="https://example.com", final_url="https://example.com",
            status_code=200, html="<h1>stealth</h1>", scrape_mode_used=ScrapeMode.STEALTH_HTTP,
        )
        await pipeline.execute(ScrapeRequest(url="https://example.com"))
        c4ai.scrape.assert_not_called()

    @pytest.mark.asyncio
    async def test_crawl4ai_fail_escalates_to_playwright(self) -> None:
        pipeline, _, c4ai, pw, _ = self._make_pipeline(stealth_ok=False, crawl4ai_ok=False, playwright_ok=True)
        result = await pipeline.execute(ScrapeRequest(url="https://example.com"))
        assert result.status_code == 200
        assert result.scrape_mode_used == ScrapeMode.PLAYWRIGHT
        c4ai.scrape.assert_called_once()
        pw.scrape.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_get_stats_does_not_raise(self) -> None:
        pipeline, *_ = self._make_pipeline()
        stats = pipeline.get_stats()
        assert isinstance(stats, dict)
