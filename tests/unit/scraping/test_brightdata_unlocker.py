"""
Unit tests for BrightDataUnlockerScraper.

All tests mock httpx to avoid real proxy calls.
Run: uv run pytest tests/unit/scraping/test_brightdata_unlocker.py -v
"""
from __future__ import annotations

import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeRequest, ScrapeResult
from searchops.scraping.brightdata_unlocker import BrightDataUnlockerScraper, build_bd_unlocker


def _make_mock_cfg(
    customer_id: str | None = "brd-customer-test123",
    zone_password: str | None = "test-zone-password",
    zone_unlocker: str = "unlocker",
    request_timeout: int = 30,
):
    """Build a minimal ScrapingSettings-like object for testing."""
    cfg = MagicMock()
    cfg.brightdata_customer_id = customer_id
    cfg.brightdata_zone_unlocker = zone_unlocker
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
    return BrightDataUnlockerScraper(valid_cfg)


@pytest.mark.asyncio
async def test_scrape_success(scraper):
    """Should return ScrapeResult with status 200 on successful proxy response."""
    target_url = "https://www.linkedin.com/company/openai"
    html_content = "<html><body><h1>OpenAI Company</h1></body></html>"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html_content
        mock_resp.content = html_content.encode()
        mock_resp.url = target_url

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await scraper.scrape(ScrapeRequest(url=target_url))

    assert result.status_code == 200
    assert result.scrape_mode_used == ScrapeMode.BD_UNLOCKER
    assert result.html == html_content
    assert result.url == target_url


@pytest.mark.asyncio
async def test_scrape_returns_403_from_upstream(scraper):
    """403 response from upstream should be returned as-is (BD didn't unblock it)."""
    target_url = "https://www.protected-site.com"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.url = target_url

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await scraper.scrape(ScrapeRequest(url=target_url))

    assert result.status_code == 403
    assert result.scrape_mode_used == ScrapeMode.BD_UNLOCKER


@pytest.mark.asyncio
async def test_scrape_network_exception_returns_500(scraper):
    """Network exceptions should be caught and returned as status 500."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client_cls.return_value = mock_client

        result = await scraper.scrape(ScrapeRequest(url="https://example.com"))

    assert result.status_code == 500
    assert result.scrape_mode_used == ScrapeMode.BD_UNLOCKER
    assert "error" in result.metadata


def test_build_bd_unlocker_returns_none_without_credentials():
    """build_bd_unlocker factory should return None gracefully when credentials absent."""
    cfg = _make_mock_cfg(customer_id=None, zone_password=None)
    result = build_bd_unlocker(cfg)
    assert result is None


def test_build_bd_unlocker_returns_scraper_with_credentials():
    """build_bd_unlocker factory should return a scraper instance when credentials are set."""
    cfg = _make_mock_cfg()
    result = build_bd_unlocker(cfg)
    assert isinstance(result, BrightDataUnlockerScraper)


def test_scraper_raises_without_credentials():
    """Constructor should raise ValueError when credentials are missing."""
    cfg = _make_mock_cfg(customer_id=None, zone_password=None)
    with pytest.raises(ValueError, match="BRIGHTDATA_CUSTOMER_ID"):
        BrightDataUnlockerScraper(cfg)


@pytest.mark.asyncio
async def test_health_check_success(scraper):
    """health_check should return True when proxy connectivity is confirmed."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        is_healthy = await scraper.health_check()

    assert is_healthy is True


@pytest.mark.asyncio
async def test_health_check_failure(scraper):
    """health_check should return False when proxy is unreachable."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Proxy timeout"))
        mock_client_cls.return_value = mock_client

        is_healthy = await scraper.health_check()

    assert is_healthy is False


@pytest.mark.asyncio
async def test_proxy_url_construction():
    """Verify proxy URL is correctly built from customer_id, zone, and password."""
    cfg = _make_mock_cfg(
        customer_id="brd-customer-abc123",
        zone_password="my-secret-pwd",
        zone_unlocker="unlocker",
    )
    scraper = BrightDataUnlockerScraper(cfg)
    assert "brd-customer-abc123" in scraper._proxy_url
    assert "unlocker" in scraper._proxy_url
    assert "my-secret-pwd" in scraper._proxy_url
    assert "brd.superproxy.io:22225" in scraper._proxy_url


@pytest.mark.asyncio
async def test_scrape_many_returns_multiple_results(scraper):
    """scrape_many should return a result for each input request."""
    urls = ["https://example1.com", "https://example2.com", "https://example3.com"]

    async def mock_scrape(req: ScrapeRequest) -> ScrapeResult:
        return ScrapeResult(
            url=req.url,
            final_url=req.url,
            status_code=200,
            scrape_mode_used=ScrapeMode.BD_UNLOCKER,
        )

    with patch.object(scraper, "scrape", side_effect=mock_scrape):
        requests = [ScrapeRequest(url=u) for u in urls]
        results = await scraper.scrape_many(requests)

    assert len(results) == 3
    assert all(r.status_code == 200 for r in results)
