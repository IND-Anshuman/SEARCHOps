"""
Unit tests for BrightDataBrowserScraper.

All tests mock Playwright CDP to avoid real cloud browser calls.
Run: uv run pytest tests/unit/scraping/test_brightdata_browser.py -v
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.brightdata_browser import BrightDataBrowserScraper, build_bd_browser


def _make_mock_cfg(
    customer_id: str | None = "brd-customer-test123",
    zone_password: str | None = "test-zone-password",
    zone_scraping_browser: str = "scraping_browser",
    request_timeout: int = 30,
):
    cfg = MagicMock()
    cfg.brightdata_customer_id = customer_id
    cfg.brightdata_zone_scraping_browser = zone_scraping_browser
    cfg.brightdata_zone_password = (
        MagicMock(get_secret_value=lambda: zone_password) if zone_password else None
    )
    cfg.request_timeout = request_timeout
    return cfg


@pytest.fixture
def valid_cfg():
    return _make_mock_cfg()


@pytest.fixture
def scraper(valid_cfg):
    return BrightDataBrowserScraper(valid_cfg)


def _make_playwright_mocks(html_content: str, title: str, final_url: str):
    """Build a mock Playwright context hierarchy."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.content = AsyncMock(return_value=html_content)
    mock_page.title = AsyncMock(return_value=title)
    mock_page.url = final_url
    mock_page.wait_for_selector = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")

    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    mock_p = AsyncMock()
    mock_p.chromium = mock_chromium

    mock_playwright_ctx = AsyncMock()
    mock_playwright_ctx.__aenter__ = AsyncMock(return_value=mock_p)
    mock_playwright_ctx.__aexit__ = AsyncMock(return_value=False)

    return mock_playwright_ctx, mock_page, mock_browser


@pytest.mark.asyncio
async def test_scrape_success(scraper):
    """Should return ScrapeResult with status 200 for a successful cloud browser fetch."""
    html = "<html><body><h1>Dynamic Page</h1></body></html>"
    mock_ctx, mock_page, mock_browser = _make_playwright_mocks(
        html_content=html,
        title="Dynamic Page",
        final_url="https://example.com/final",
    )

    with patch("searchops.scraping.brightdata_browser.async_playwright", return_value=mock_ctx):
        result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

    assert result.status_code == 200
    assert result.scrape_mode_used == ScrapeMode.BD_BROWSER
    assert result.html == html
    assert result.title == "Dynamic Page"
    assert result.final_url == "https://example.com/final"


@pytest.mark.asyncio
async def test_scrape_with_wait_for_selector(scraper):
    """wait_for_selector should be called when specified in the request."""
    html = "<html><body><div class='loaded'>Content</div></body></html>"
    mock_ctx, mock_page, _ = _make_playwright_mocks(
        html_content=html,
        title="Lazy Page",
        final_url="https://spa.example.com",
    )

    with patch("searchops.scraping.brightdata_browser.async_playwright", return_value=mock_ctx):
        await scraper.scrape(
            ScrapeRequest(url="https://spa.example.com", wait_for_selector=".loaded")
        )

    mock_page.wait_for_selector.assert_called_once_with(".loaded", timeout=15_000)


@pytest.mark.asyncio
async def test_scrape_takes_screenshot(scraper):
    """take_screenshot=True should call page.screenshot and include base64 in result."""
    html = "<html><body>Screenshot Test</body></html>"
    mock_ctx, mock_page, _ = _make_playwright_mocks(
        html_content=html, title="SS Page", final_url="https://example.com"
    )

    with patch("searchops.scraping.brightdata_browser.async_playwright", return_value=mock_ctx):
        result = await scraper.scrape(
            ScrapeRequest(url="https://example.com", take_screenshot=True)
        )

    mock_page.screenshot.assert_called_once()
    assert result.screenshot_base64 is not None
    assert len(result.screenshot_base64) > 0


@pytest.mark.asyncio
async def test_scrape_exception_returns_500(scraper):
    """Playwright CDP exceptions should be caught and returned as status 500."""
    mock_p = AsyncMock()
    mock_p.chromium.connect_over_cdp = AsyncMock(
        side_effect=Exception("CDP WebSocket connection failed")
    )

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_p)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("searchops.scraping.brightdata_browser.async_playwright", return_value=mock_ctx):
        result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

    assert result.status_code == 500
    assert result.scrape_mode_used == ScrapeMode.BD_BROWSER
    assert "error" in result.metadata


def test_build_bd_browser_returns_none_without_credentials():
    """Factory should return None when credentials are not configured."""
    cfg = _make_mock_cfg(customer_id=None, zone_password=None)
    result = build_bd_browser(cfg)
    assert result is None


def test_build_bd_browser_returns_instance_with_credentials():
    """Factory should return BrightDataBrowserScraper when credentials are set."""
    cfg = _make_mock_cfg()
    result = build_bd_browser(cfg)
    assert isinstance(result, BrightDataBrowserScraper)


def test_constructor_raises_without_credentials():
    """Constructor should raise ValueError when customer_id or password missing."""
    cfg = _make_mock_cfg(customer_id=None, zone_password=None)
    with pytest.raises(ValueError, match="BRIGHTDATA_CUSTOMER_ID"):
        BrightDataBrowserScraper(cfg)


def test_cdp_url_construction():
    """CDP WebSocket URL should be correctly built from credentials."""
    cfg = _make_mock_cfg(
        customer_id="brd-customer-xyz789",
        zone_password="s3cr3t",
        zone_scraping_browser="scraping_browser",
    )
    s = BrightDataBrowserScraper(cfg)
    assert "brd-customer-xyz789" in s._cdp_url
    assert "scraping_browser" in s._cdp_url
    assert "s3cr3t" in s._cdp_url
    assert "brd.superproxy.io:9222" in s._cdp_url
    assert s._cdp_url.startswith("wss://")


@pytest.mark.asyncio
async def test_health_check_success(scraper):
    """health_check should return True when CDP connection succeeds."""
    mock_browser = AsyncMock()
    mock_browser.close = AsyncMock()
    mock_p = AsyncMock()
    mock_p.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_p)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("searchops.scraping.brightdata_browser.async_playwright", return_value=mock_ctx):
        result = await scraper.health_check()

    assert result is True


@pytest.mark.asyncio
async def test_health_check_failure(scraper):
    """health_check should return False when CDP connection fails."""
    mock_p = AsyncMock()
    mock_p.chromium.connect_over_cdp = AsyncMock(side_effect=Exception("Unreachable"))
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_p)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("searchops.scraping.brightdata_browser.async_playwright", return_value=mock_ctx):
        result = await scraper.health_check()

    assert result is False
