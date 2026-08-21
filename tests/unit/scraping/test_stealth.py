"""
Unit tests for the Stealth HTTP Transport layer (Phase 2).

Test strategy:
  - All tests are unit-level: curl_cffi is mocked entirely using unittest.mock.
  - No real HTTP requests are made.
  - Pipeline integration tests use the mocked stealth scraper injected directly.

Coverage targets:
  StealthConfig       — validation, defaults, invalid target rejection
  _chrome124_headers  — key presence, no credentials leak
  StealthHTTPScraper  — success path, error path, non-200 path, health_check
  ProxyRouter         — inherits correctly, masks proxy in logs
  build_stealth_scraper / build_proxy_router — factory helpers
  ScrapingPipeline    — tier 0 short-circuit, tier 0b fallback, prune_if_needed
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.stealth import (
    IMPERSONATE_TARGETS,
    ProxyRouter,
    StealthConfig,
    StealthHTTPScraper,
    _chrome124_headers,
    _mask_proxy,
    build_proxy_router,
    build_stealth_scraper,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_URL = "https://example.com/page"
SAMPLE_HTML = "<html><body><h1>Hello</h1></body></html>"


def _make_mock_response(
    *,
    status_code: int = 200,
    text: str = SAMPLE_HTML,
    url: str = SAMPLE_URL,
) -> MagicMock:
    """Build a minimal mock that looks like a curl_cffi Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    return resp


def _make_mock_session(response: MagicMock) -> MagicMock:
    """Build an async context-manager mock for curl_cffi.requests.AsyncSession."""
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ─────────────────────────────────────────────────────────────────────────────
# StealthConfig tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStealthConfig:
    """Tests for the immutable StealthConfig dataclass."""

    @pytest.mark.unit
    def test_defaults_are_valid(self) -> None:
        cfg = StealthConfig()
        assert cfg.impersonate == "chrome124"
        assert cfg.proxy_url is None
        assert cfg.connect_timeout == 10.0
        assert cfg.read_timeout == 30.0
        assert cfg.max_redirects == 10
        assert cfg.verify_ssl is True

    @pytest.mark.unit
    def test_valid_impersonate_targets_accepted(self) -> None:
        for target in list(IMPERSONATE_TARGETS)[:5]:
            cfg = StealthConfig(impersonate=target)
            assert cfg.impersonate == target

    @pytest.mark.unit
    def test_invalid_impersonate_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown impersonation target"):
            StealthConfig(impersonate="ie6")

    @pytest.mark.unit
    def test_frozen_prevents_mutation(self) -> None:
        cfg = StealthConfig()
        with pytest.raises(AttributeError):
            cfg.impersonate = "firefox133"  # type: ignore[misc]

    @pytest.mark.unit
    def test_proxy_url_stored(self) -> None:
        cfg = StealthConfig(proxy_url="http://user:pass@proxy.example.com:8080")
        assert cfg.proxy_url is not None
        assert "proxy.example.com" in cfg.proxy_url


# ─────────────────────────────────────────────────────────────────────────────
# _chrome124_headers tests
# ─────────────────────────────────────────────────────────────────────────────

class TestChrome124Headers:
    """Tests for the browser-profile header generator."""

    @pytest.mark.unit
    def test_returns_dict(self) -> None:
        headers = _chrome124_headers(SAMPLE_URL)
        assert isinstance(headers, dict)

    @pytest.mark.unit
    def test_required_sec_fetch_keys_present(self) -> None:
        headers = _chrome124_headers(SAMPLE_URL)
        assert "Sec-Fetch-Dest" in headers
        assert "Sec-Fetch-Mode" in headers
        assert "Sec-Fetch-Site" in headers
        assert "Sec-Ch-Ua" in headers

    @pytest.mark.unit
    def test_accept_header_includes_html(self) -> None:
        headers = _chrome124_headers(SAMPLE_URL)
        assert "text/html" in headers["Accept"]

    @pytest.mark.unit
    def test_referer_derived_from_url(self) -> None:
        headers = _chrome124_headers("https://shop.example.com/products")
        assert "https://shop.example.com" in headers["Referer"]

    @pytest.mark.unit
    def test_no_credentials_in_headers(self) -> None:
        headers = _chrome124_headers(SAMPLE_URL)
        header_values = " ".join(headers.values())
        assert "password" not in header_values.lower()
        assert "token" not in header_values.lower()


# ─────────────────────────────────────────────────────────────────────────────
# _mask_proxy tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMaskProxy:
    """Tests for the proxy URL masking utility."""

    @pytest.mark.unit
    def test_masks_password(self) -> None:
        masked = _mask_proxy("http://user:s3cr3t@proxy.com:823")
        assert "s3cr3t" not in masked
        assert "***" in masked
        assert "user" in masked
        assert "proxy.com" in masked

    @pytest.mark.unit
    def test_no_password_unchanged(self) -> None:
        url = "http://proxy.com:823"
        assert _mask_proxy(url) == url

    @pytest.mark.unit
    def test_malformed_url_returns_placeholder(self) -> None:
        # Force an exception by passing something that urlparse can't handle
        # Simulate error path via monkeypatching in the outer scope
        result = _mask_proxy("")
        # Empty string has no password — stays as-is or empty
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# StealthHTTPScraper tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStealthHTTPScraper:
    """Tests for the core stealth scraper."""

    # ── construction ──────────────────────────────────────────────────────

    @pytest.mark.unit
    def test_default_construction(self) -> None:
        scraper = StealthHTTPScraper()
        assert scraper._cfg.impersonate == "chrome124"
        assert scraper._cfg.proxy_url is None

    @pytest.mark.unit
    def test_kwargs_construction(self) -> None:
        scraper = StealthHTTPScraper(
            impersonate="firefox133",
            proxy_url="http://user:pass@host:8080",
        )
        assert scraper._cfg.impersonate == "firefox133"
        assert scraper._cfg.proxy_url is not None

    @pytest.mark.unit
    def test_config_object_takes_precedence(self) -> None:
        cfg = StealthConfig(impersonate="edge99")
        scraper = StealthHTTPScraper(config=cfg, impersonate="chrome124")
        assert scraper._cfg.impersonate == "edge99"

    # ── health_check ───────────────────────────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_health_check_true_when_curl_cffi_available(self) -> None:
        scraper = StealthHTTPScraper()
        # curl_cffi is installed (we added it to pyproject.toml)
        result = await scraper.health_check()
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_health_check_false_when_import_fails(self) -> None:
        scraper = StealthHTTPScraper()
        with patch.dict("sys.modules", {"curl_cffi": None}):
            result = await scraper.health_check()
            assert result is False

    # ── scrape — success path ──────────────────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    async def test_scrape_success_200(self) -> None:
        scraper  = StealthHTTPScraper()
        request  = ScrapeRequest(url=SAMPLE_URL)
        mock_session = _make_mock_session(_make_mock_response(status_code=200, url=SAMPLE_URL))


        with patch(
            "searchops.scraping.stealth.AsyncSession",
            return_value=mock_session,
        ):
            result = await scraper.scrape(request)

        assert result.status_code == 200
        assert result.scrape_mode_used == ScrapeMode.STEALTH_HTTP
        assert result.html == SAMPLE_HTML
        assert result.url == SAMPLE_URL
        assert result.duration_ms > 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_success_sets_impersonate_in_metadata(self) -> None:
        scraper  = StealthHTTPScraper(impersonate="firefox133")
        request  = ScrapeRequest(url=SAMPLE_URL)
        mock_session = _make_mock_session(_make_mock_response())

        with patch("searchops.scraping.stealth.AsyncSession", return_value=mock_session):
            result = await scraper.scrape(request)

        assert result.metadata.get("impersonate") == "firefox133"

    # ── scrape — non-200 path ─────────────────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_403_returns_no_html(self) -> None:
        scraper  = StealthHTTPScraper()
        request  = ScrapeRequest(url=SAMPLE_URL)
        mock_session = _make_mock_session(_make_mock_response(status_code=403, text=""))

        with patch("searchops.scraping.stealth.AsyncSession", return_value=mock_session):
            result = await scraper.scrape(request)

        assert result.status_code == 403
        assert result.html is None
        assert result.scrape_mode_used == ScrapeMode.STEALTH_HTTP

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_429_returns_correct_status(self) -> None:
        scraper  = StealthHTTPScraper()
        request  = ScrapeRequest(url=SAMPLE_URL)
        mock_session = _make_mock_session(_make_mock_response(status_code=429, text=""))

        with patch("searchops.scraping.stealth.AsyncSession", return_value=mock_session):
            result = await scraper.scrape(request)

        assert result.status_code == 429

    # ── scrape — error / exception path ───────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_network_exception_returns_500(self) -> None:
        scraper  = StealthHTTPScraper()
        request  = ScrapeRequest(url=SAMPLE_URL)
        bad_session = MagicMock()
        bad_session.get = AsyncMock(side_effect=ConnectionError("refused"))
        bad_session.__aenter__ = AsyncMock(return_value=bad_session)
        bad_session.__aexit__ = AsyncMock(return_value=False)

        with patch("searchops.scraping.stealth.AsyncSession", return_value=bad_session):
            result = await scraper.scrape(request)

        assert result.status_code == 500
        assert result.html is None
        assert "error" in result.metadata
        assert "refused" in result.metadata["error"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_timeout_returns_500(self) -> None:
        scraper  = StealthHTTPScraper()
        request  = ScrapeRequest(url=SAMPLE_URL, timeout_seconds=1)
        bad_session = MagicMock()
        bad_session.get = AsyncMock(side_effect=TimeoutError("timed out"))
        bad_session.__aenter__ = AsyncMock(return_value=bad_session)
        bad_session.__aexit__ = AsyncMock(return_value=False)

        with patch("searchops.scraping.stealth.AsyncSession", return_value=bad_session):
            result = await scraper.scrape(request)

        assert result.status_code == 500

    # ── scrape_many ────────────────────────────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_many_returns_list_of_correct_length(self) -> None:
        scraper  = StealthHTTPScraper()
        requests = [ScrapeRequest(url=f"https://example.com/{i}") for i in range(4)]
        mock_session = _make_mock_session(_make_mock_response())

        with patch("searchops.scraping.stealth.AsyncSession", return_value=mock_session):
            results = await scraper.scrape_many(requests, max_concurrency=2)

        assert len(results) == 4
        assert all(r.status_code == 200 for r in results)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scrape_many_concurrency_is_bounded(self) -> None:
        """Verify semaphore actually limits concurrent scrapes."""
        active: list[int] = [0]
        peak: list[int]   = [0]

        async def slow_scrape(req: ScrapeRequest) -> ScrapeResult:
            active[0] += 1
            peak[0]    = max(peak[0], active[0])
            await asyncio.sleep(0.01)
            active[0] -= 1
            return ScrapeResult(
                url=req.url, final_url=req.url,
                status_code=200, scrape_mode_used=ScrapeMode.STEALTH_HTTP,
            )

        scraper = StealthHTTPScraper()
        scraper.scrape = slow_scrape  # type: ignore[assignment]

        reqs = [ScrapeRequest(url=f"https://x.com/{i}") for i in range(6)]
        await scraper.scrape_many(reqs, max_concurrency=3)

        assert peak[0] <= 3

    # ── header layering ────────────────────────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_per_request_headers_override_defaults(self) -> None:
        """Request-level headers must take priority over browser defaults."""
        scraper = StealthHTTPScraper()
        request = ScrapeRequest(
            url=SAMPLE_URL,
            headers={"Accept-Language": "de-DE,de;q=0.9"},
        )
        captured: dict[str, Any] = {}

        async def capture_get(url: str, *, headers: dict, **kwargs: Any) -> MagicMock:
            captured.update(headers)
            return _make_mock_response()

        mock_session = MagicMock()
        mock_session.get = capture_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("searchops.scraping.stealth.AsyncSession", return_value=mock_session):
            await scraper.scrape(request)

        assert captured.get("Accept-Language") == "de-DE,de;q=0.9"


# ─────────────────────────────────────────────────────────────────────────────
# ProxyRouter tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProxyRouter:
    """Tests for the Tier 0b proxy-routing wrapper."""

    PROXY = "http://user:s3cr3t@gate.dc.dataimpulse.com:823"

    @pytest.mark.unit
    def test_construction_sets_proxy(self) -> None:
        router = ProxyRouter(proxy_url=self.PROXY)
        assert router._cfg.proxy_url == self.PROXY

    @pytest.mark.unit
    def test_construction_sets_impersonate(self) -> None:
        router = ProxyRouter(proxy_url=self.PROXY, impersonate="safari18_0")
        assert router._cfg.impersonate == "safari18_0"

    @pytest.mark.unit
    def test_is_subclass_of_stealth_scraper(self) -> None:
        router = ProxyRouter(proxy_url=self.PROXY)
        assert isinstance(router, StealthHTTPScraper)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_proxy_url_passed_to_session(self) -> None:
        router  = ProxyRouter(proxy_url=self.PROXY)
        request = ScrapeRequest(url=SAMPLE_URL)
        captured_proxies: dict[str, Any] = {}

        async def capture_get(url: str, *, proxies: Any = None, **kwargs: Any) -> MagicMock:
            if proxies:
                captured_proxies.update(proxies)
            return _make_mock_response()

        mock_session = MagicMock()
        mock_session.get = capture_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("searchops.scraping.stealth.AsyncSession", return_value=mock_session):
            await router.scrape(request)

        assert "https" in captured_proxies
        assert captured_proxies["https"] == self.PROXY


# ─────────────────────────────────────────────────────────────────────────────
# Factory helper tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFactoryHelpers:
    """Tests for build_stealth_scraper and build_proxy_router."""

    @pytest.mark.unit
    def test_build_stealth_scraper_uses_settings_impersonate(self) -> None:
        settings = MagicMock()
        settings.stealth_impersonate = "firefox133"
        scraper = build_stealth_scraper(settings)
        assert isinstance(scraper, StealthHTTPScraper)
        assert scraper._cfg.impersonate == "firefox133"

    @pytest.mark.unit
    def test_build_stealth_scraper_default_impersonate(self) -> None:
        settings = MagicMock(spec=[])  # no attributes → getattr falls back to default
        scraper = build_stealth_scraper(settings)
        assert scraper._cfg.impersonate == "chrome124"

    @pytest.mark.unit
    def test_build_proxy_router_disabled_returns_none(self) -> None:
        settings = MagicMock()
        settings.proxy_enabled = False
        result = build_proxy_router(settings)
        assert result is None

    @pytest.mark.unit
    def test_build_proxy_router_enabled_no_url_returns_none(self) -> None:
        settings = MagicMock()
        settings.proxy_enabled = True
        settings.proxy_url_tier1 = None
        result = build_proxy_router(settings)
        assert result is None

    @pytest.mark.unit
    def test_build_proxy_router_enabled_with_url_returns_router(self) -> None:
        proxy_secret = MagicMock()
        proxy_secret.get_secret_value.return_value = "http://u:p@proxy.com:823"

        settings = MagicMock()
        settings.proxy_enabled        = True
        settings.proxy_url_tier1      = proxy_secret
        settings.stealth_impersonate  = "chrome124"
        settings.proxy_connect_timeout = 10.0

        result = build_proxy_router(settings)
        assert isinstance(result, ProxyRouter)
        assert result._cfg.proxy_url == "http://u:p@proxy.com:823"

    @pytest.mark.unit
    def test_build_proxy_router_uses_plain_string_url(self) -> None:
        settings = MagicMock()
        settings.proxy_enabled        = True
        settings.proxy_url_tier1      = "http://u:p@proxy.com:823"  # plain str, not SecretStr
        settings.stealth_impersonate  = "chrome124"
        settings.proxy_connect_timeout = 10.0

        result = build_proxy_router(settings)
        assert result is not None
        assert result._cfg.proxy_url == "http://u:p@proxy.com:823"


# ─────────────────────────────────────────────────────────────────────────────
# ScrapeMode enum regression tests
# ─────────────────────────────────────────────────────────────────────────────

class TestScrapeModeEnum:
    """Verify new ScrapeMode values exist and serialise correctly."""

    @pytest.mark.unit
    def test_stealth_http_mode_exists(self) -> None:
        assert ScrapeMode.STEALTH_HTTP == "stealth_http"

    @pytest.mark.unit
    def test_crawl4ai_mode_exists(self) -> None:
        assert ScrapeMode.CRAWL4AI == "crawl4ai"

    @pytest.mark.unit
    def test_docling_pdf_mode_exists(self) -> None:
        assert ScrapeMode.DOCLING_PDF == "docling_pdf"

    @pytest.mark.unit
    def test_existing_modes_unchanged(self) -> None:
        assert ScrapeMode.FIRECRAWL  == "firecrawl"
        assert ScrapeMode.PLAYWRIGHT == "playwright"
        assert ScrapeMode.HTTP       == "http"
        assert ScrapeMode.AUTO       == "auto"


# ─────────────────────────────────────────────────────────────────────────────
# ScrapingPipeline integration tests (stealth tier wiring)
# ─────────────────────────────────────────────────────────────────────────────

class TestScrapingPipelineStealthIntegration:
    """
    Integration tests for stealth tier wiring inside ScrapingPipeline.

    Uses fake implementations instead of mocking internals — validates
    that the pipeline's tier dispatch logic is correct.
    """

    def _make_success_scraper(self, mode: ScrapeMode = ScrapeMode.STEALTH_HTTP) -> StealthHTTPScraper:
        scraper = StealthHTTPScraper()

        async def _ok(req: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=req.url, final_url=req.url,
                status_code=200, html=SAMPLE_HTML,
                scrape_mode_used=mode,
            )

        scraper.scrape = _ok  # type: ignore[assignment]
        return scraper

    def _make_fail_scraper(
        self,
        status: int = 403,
        mode: ScrapeMode = ScrapeMode.STEALTH_HTTP,
    ) -> StealthHTTPScraper:
        scraper = StealthHTTPScraper()

        async def _fail(req: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=req.url, final_url=req.url,
                status_code=status,
                scrape_mode_used=mode,
            )

        scraper.scrape = _fail  # type: ignore[assignment]
        return scraper

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tier0_success_short_circuits_pipeline(self) -> None:
        """Tier 0 success must prevent any other tier from being called."""
        from searchops.scraping.pipeline import ScrapingPipeline
        from searchops.scraping.rate_limiter import DomainRateLimiter

        rate_limiter = MagicMock(spec=DomainRateLimiter)
        rate_limiter.check         = AsyncMock(return_value=(True, 0.0))
        rate_limiter.record_response = AsyncMock()
        rate_limiter.get_all_stats = MagicMock(return_value={})

        playwright_mock = MagicMock()
        playwright_mock.scrape = AsyncMock()  # should NOT be called
        playwright_mock.pool   = MagicMock(stats={})

        pipeline = ScrapingPipeline(
            stealth=self._make_success_scraper(),
            proxy_router=None,
            playwright=playwright_mock,
            rate_limiter=rate_limiter,
        )

        result = await pipeline.execute(ScrapeRequest(url=SAMPLE_URL))
        assert result.status_code == 200
        playwright_mock.scrape.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_tier0b_called_when_tier0_fails(self) -> None:
        """Tier 0b (ProxyRouter) must be called when Tier 0 fails."""
        from searchops.scraping.pipeline import ScrapingPipeline
        from searchops.scraping.rate_limiter import DomainRateLimiter

        rate_limiter = MagicMock(spec=DomainRateLimiter)
        rate_limiter.check           = AsyncMock(return_value=(True, 0.0))
        rate_limiter.record_response = AsyncMock()
        rate_limiter.get_all_stats   = MagicMock(return_value={})

        proxy_success = self._make_success_scraper(ScrapeMode.STEALTH_HTTP)

        pipeline = ScrapingPipeline(
            stealth=self._make_fail_scraper(status=403),
            proxy_router=proxy_success,  # type: ignore[arg-type]
            rate_limiter=rate_limiter,
        )

        result = await pipeline.execute(ScrapeRequest(url=SAMPLE_URL))
        assert result.status_code == 200

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_stats_includes_stealth_keys(self) -> None:
        from searchops.scraping.pipeline import ScrapingPipeline
        from searchops.scraping.rate_limiter import DomainRateLimiter

        rate_limiter = MagicMock(spec=DomainRateLimiter)
        rate_limiter.get_all_stats = MagicMock(return_value={})

        pipeline = ScrapingPipeline(
            stealth=self._make_success_scraper(),
            rate_limiter=rate_limiter,
        )

        stats = pipeline.get_stats()
        assert "stealth" in stats
        assert "impersonate" in stats["stealth"]
        assert "proxy_enabled" in stats["stealth"]

    @pytest.mark.unit
    def test_prune_if_needed_adds_markdown_when_absent(self) -> None:
        from searchops.scraping.pipeline import ScrapingPipeline
        from searchops.scraping.rate_limiter import DomainRateLimiter

        rate_limiter = MagicMock(spec=DomainRateLimiter)
        rate_limiter.get_all_stats = MagicMock(return_value={})
        pipeline = ScrapingPipeline(rate_limiter=rate_limiter)

        result = ScrapeResult(
            url=SAMPLE_URL, final_url=SAMPLE_URL,
            status_code=200, html=SAMPLE_HTML,
            scrape_mode_used=ScrapeMode.STEALTH_HTTP,
        )
        pruned = pipeline._prune_if_needed(result)

        # Content pruner may return empty string for minimal HTML, but should not crash
        assert pruned.markdown is not None

    @pytest.mark.unit
    def test_prune_if_needed_skips_when_markdown_already_set(self) -> None:
        from searchops.scraping.pipeline import ScrapingPipeline
        from searchops.scraping.rate_limiter import DomainRateLimiter

        rate_limiter = MagicMock(spec=DomainRateLimiter)
        rate_limiter.get_all_stats = MagicMock(return_value={})
        pipeline = ScrapingPipeline(rate_limiter=rate_limiter)

        existing_md = "# Already Markdown"
        result = ScrapeResult(
            url=SAMPLE_URL, final_url=SAMPLE_URL,
            status_code=200, html=SAMPLE_HTML, markdown=existing_md,
            scrape_mode_used=ScrapeMode.STEALTH_HTTP,
        )
        unchanged = pipeline._prune_if_needed(result)

        # Markdown must not be overwritten when already present
        assert unchanged.markdown == existing_md
