"""
Stealth HTTP Scraper — JA4 TLS Bypass via curl_cffi BoringSSL.

Standard Python HTTP clients (httpx, aiohttp, requests) emit a distinctive
TLS 1.3 Client Hello fingerprint (JA4 signature) that WAFs like Cloudflare,
Akamai DataStream, DataDome, and PerimeterX/HUMAN detect and block instantly.

This module provides a curl_cffi-backed scraper that impersonates real browser
TLS stacks (Chrome 124 / Firefox 133 / Safari 18) at the libcurl level,
defeating JA4-based bot detection without spinning up a headless browser.

Tiers in the pipeline:
  Tier 0   StealthHTTPScraper  — curl_cffi direct, zero cost, ~150 ms
  Tier 0b  ProxyRouter         — curl_cffi + DataImpulse residential, ~400 ms

WAF bypass success rates (Research Report 2 Anti-Bot Benchmark Matrix):
  plain httpx (OpenSSL)    →  ~33 % on WAF-protected targets
  curl_cffi direct         →  ~70 %
  curl_cffi + resi proxy   →  ~90 %+

Usage::

    from searchops.scraping.stealth import StealthHTTPScraper, ProxyRouter

    # Tier 0 — no proxy
    scraper = StealthHTTPScraper()
    result  = await scraper.scrape(ScrapeRequest(url="https://example.com"))

    # Tier 0b — proxied
    proxied = ProxyRouter(proxy_url="http://user:pass@gate.dc.dataimpulse.com:823")
    result  = await proxied.scrape(ScrapeRequest(url="https://cf-protected.com"))
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import structlog

from searchops.core.interfaces.scraper import IScraper, ScrapeMode, ScrapeRequest, ScrapeResult

log = structlog.get_logger(__name__)

# Module-level import so patch() can intercept it in tests.
# Falls back gracefully when curl_cffi is not installed.
try:
    from curl_cffi.requests import AsyncSession
except ImportError:  # pragma: no cover
    AsyncSession = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Supported impersonation targets (curl_cffi 0.7+)
# ---------------------------------------------------------------------------
IMPERSONATE_TARGETS: frozenset[str] = frozenset({
    "chrome99", "chrome100", "chrome101", "chrome104", "chrome107",
    "chrome110", "chrome116", "chrome119", "chrome120", "chrome123",
    "chrome124",   # ← default: best overall WAF bypass rate as of 2025-Q1
    "chrome131", "chrome133",
    "chrome99_android",
    "firefox91esr", "firefox95", "firefox98", "firefox100",
    "firefox102", "firefox104", "firefox109", "firefox117",
    "firefox120", "firefox121", "firefox123", "firefox133",
    "safari15_3", "safari15_5", "safari16", "safari16_5",
    "safari17_0", "safari17_2_ios", "safari18_0",
    "edge99", "edge101",
})

_DEFAULT_IMPERSONATE = "chrome124"
_DEFAULT_TIMEOUT     = 30.0  # seconds


# ---------------------------------------------------------------------------
# Immutable configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StealthConfig:
    """
    Immutable configuration for the stealth transport layer.

    All fields carry production-safe defaults.  Override via env vars
    SCRAPING_STEALTH_IMPERSONATE and PROXY_URL_TIER1 (see ScrapingSettings).
    """

    impersonate: str = _DEFAULT_IMPERSONATE
    """Browser TLS fingerprint to impersonate. Must be in IMPERSONATE_TARGETS."""

    proxy_url: str | None = None
    """Optional proxy URL.  None = direct connection (Tier 0)."""

    connect_timeout: float = 10.0
    """TCP + TLS handshake timeout in seconds."""

    read_timeout: float = 30.0
    """Full response body read timeout in seconds."""

    max_redirects: int = 10
    """Maximum HTTP redirects to follow."""

    verify_ssl: bool = True
    """Validate server TLS certificate.  False only for dev/testing."""

    extra_headers: dict[str, str] = field(default_factory=dict)
    """Additional headers merged on top of the default browser profile."""

    def __post_init__(self) -> None:
        if self.impersonate not in IMPERSONATE_TARGETS:
            raise ValueError(
                f"Unknown impersonation target {self.impersonate!r}. "
                f"Valid values: {sorted(IMPERSONATE_TARGETS)}"
            )


# ---------------------------------------------------------------------------
# Browser-like request headers (Chrome 124 profile)
# ---------------------------------------------------------------------------

def _chrome124_headers(url: str) -> dict[str, str]:
    """
    Return a realistic Chrome 124 Accept/Sec-Fetch header set for *url*.

    These are secondary anti-bot signals that WAFs inspect alongside JA4.
    Matching them to the impersonated browser avoids header-fingerprint mismatches.
    """
    parsed = urlparse(url)
    return {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;"
            "q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": (
            '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": f"{parsed.scheme}://{parsed.netloc}",
    }


# ---------------------------------------------------------------------------
# StealthHTTPScraper  (Tier 0 — direct, no proxy)
# ---------------------------------------------------------------------------

class StealthHTTPScraper(IScraper):
    """
    JA4 TLS-spoofing HTTP scraper backed by curl_cffi / BoringSSL.

    Sends HTTP requests through libcurl compiled with the same BoringSSL
    library that Chrome uses, making the TLS Client Hello fingerprint
    indistinguishable from a real browser at the network layer.

    The scraper is **stateless** — each call opens a short-lived AsyncSession.
    It is safe to share one instance across many concurrent coroutines.

    Attributes:
        _cfg:  Immutable :class:`StealthConfig` governing TLS and proxy behaviour.

    Example::

        scraper = StealthHTTPScraper()
        result  = await scraper.scrape(ScrapeRequest(url="https://example.com"))
        # result.html  — raw HTML (markdown pruning happens downstream)
        # result.scrape_mode_used == ScrapeMode.STEALTH_HTTP
    """

    def __init__(
        self,
        config: StealthConfig | None = None,
        *,
        impersonate: str = _DEFAULT_IMPERSONATE,
        proxy_url: str | None = None,
    ) -> None:
        self._cfg = config or StealthConfig(
            impersonate=impersonate,
            proxy_url=proxy_url,
        )
        log.debug(
            "StealthHTTPScraper ready",
            impersonate=self._cfg.impersonate,
            proxy=bool(self._cfg.proxy_url),
        )

    # ------------------------------------------------------------------ #
    #  IScraper protocol implementation                                     #
    # ------------------------------------------------------------------ #

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """
        Fetch *request.url* with browser-impersonated TLS and return a ScrapeResult.

        On success (status 200):  ``result.html`` is populated with response text.
        On non-200 or error:      ``result.html`` is None; ``result.metadata["error"]``
                                  contains the reason.

        ``result.markdown`` is always ``None`` here — pass the result through
        ``ContentPruner`` in the pipeline for HTML→Markdown conversion.
        """
        log.info(
            "stealth.scrape",
            url=request.url,
            impersonate=self._cfg.impersonate,
            proxy=bool(self._cfg.proxy_url),
        )
        start = time.perf_counter()

        try:
            html, final_url, status = await self._fetch(request)
        except Exception as exc:
            elapsed = round((time.perf_counter() - start) * 1000, 1)
            log.error("stealth.scrape.failed", url=request.url, error=str(exc), duration_ms=elapsed)
            return ScrapeResult(
                url=request.url,
                final_url=request.url,
                status_code=500,
                scrape_mode_used=ScrapeMode.STEALTH_HTTP,
                duration_ms=elapsed,
                metadata={"error": str(exc), "impersonate": self._cfg.impersonate},
            )

        elapsed = round((time.perf_counter() - start) * 1000, 1)
        log.info(
            "stealth.scrape.done",
            url=request.url,
            final_url=final_url,
            status=status,
            duration_ms=elapsed,
        )
        return ScrapeResult(
            url=request.url,
            final_url=final_url,
            status_code=status,
            html=html,
            scrape_mode_used=ScrapeMode.STEALTH_HTTP,
            duration_ms=elapsed,
            metadata={
                "impersonate": self._cfg.impersonate,
                "proxy": bool(self._cfg.proxy_url),
            },
        )

    async def scrape_many(
        self,
        requests: list[ScrapeRequest],
        *,
        max_concurrency: int = 10,
    ) -> list[ScrapeResult]:
        """
        Scrape multiple URLs concurrently bounded by *max_concurrency*.

        curl_cffi uses a shared libcurl multi-handle, so high concurrency is
        efficient.  Default ceiling (10) avoids tripping server connection limits.
        """
        sem = asyncio.Semaphore(max_concurrency)

        async def _bounded(req: ScrapeRequest) -> ScrapeResult:
            async with sem:
                return await self.scrape(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def health_check(self) -> bool:
        """Return True when curl_cffi is importable (package is installed)."""
        try:
            import curl_cffi  # noqa: F401
            return True
        except ImportError:
            log.error("curl_cffi not installed — StealthHTTPScraper unavailable")
            return False

    # ------------------------------------------------------------------ #
    #  Internal fetch                                                       #
    # ------------------------------------------------------------------ #

    async def _fetch(
        self,
        request: ScrapeRequest,
    ) -> tuple[str | None, str, int]:
        """
        Run the actual curl_cffi HTTP request.

        Returns:
            ``(html_text_or_None, final_url_after_redirects, http_status_code)``
        """
        if AsyncSession is None:  # pragma: no cover
            raise RuntimeError(
                "curl_cffi is not installed. Run: uv add 'curl-cffi>=0.7.0'"
            )

        timeout  = float(request.timeout_seconds or self._cfg.read_timeout)
        proxies: dict[str, str] | None = (
            {"https": self._cfg.proxy_url, "http": self._cfg.proxy_url}
            if self._cfg.proxy_url
            else None
        )

        # Layer headers: browser defaults → config extras → per-request overrides
        headers = {
            **_chrome124_headers(request.url),
            **self._cfg.extra_headers,
            **request.headers,
        }

        async with AsyncSession(
            impersonate=self._cfg.impersonate,
            verify=self._cfg.verify_ssl,
            max_redirects=self._cfg.max_redirects,
        ) as session:
            resp = await session.get(
                request.url,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
            )

        return (
            resp.text if resp.status_code == 200 else None,
            str(resp.url),
            resp.status_code,
        )



# ---------------------------------------------------------------------------
# ProxyRouter  (Tier 0b — residential proxy wrapper)
# ---------------------------------------------------------------------------

class ProxyRouter(StealthHTTPScraper):
    """
    Tier 0b: curl_cffi stealth transport routed through a residential proxy.

    Identical to ``StealthHTTPScraper`` except all requests are tunnelled
    through the provided *proxy_url*, masking the local IP from target servers.

    Proxy credentials are never logged in plaintext (masked in debug output).

    Cost model:  ~$1/GB (DataImpulse)  ≈ $0.001 per average 1 MB page.
    Latency:     +250–300 ms over direct connection.

    Example::

        router = ProxyRouter(
            proxy_url="http://user:pass@gate.dc.dataimpulse.com:823",
        )
        result = await router.scrape(ScrapeRequest(url="https://target.com"))
    """

    def __init__(
        self,
        proxy_url: str,
        impersonate: str = _DEFAULT_IMPERSONATE,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            config=StealthConfig(
                impersonate=impersonate,
                proxy_url=proxy_url,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
            )
        )
        log.debug("ProxyRouter ready", proxy_host=_mask_proxy(proxy_url))


# ---------------------------------------------------------------------------
# Factory helpers — wired to application settings
# ---------------------------------------------------------------------------

def build_stealth_scraper(settings: Any | None = None) -> StealthHTTPScraper:
    """
    Build a ``StealthHTTPScraper`` (Tier 0) from application settings.

    Uses ``ScrapingSettings`` when *settings* is None.
    No proxy is configured — use :func:`build_proxy_router` for Tier 0b.

    Args:
        settings: A ``ScrapingSettings``-like object or None (auto-resolved).

    Returns:
        Ready-to-use :class:`StealthHTTPScraper`.
    """
    if settings is None:
        from searchops.config.settings import get_settings
        settings = get_settings().scraping

    return StealthHTTPScraper(
        config=StealthConfig(
            impersonate=getattr(settings, "stealth_impersonate", _DEFAULT_IMPERSONATE),
        )
    )


def build_proxy_router(settings: Any | None = None) -> ProxyRouter | None:
    """
    Build a :class:`ProxyRouter` (Tier 0b) from application settings.

    Returns ``None`` when proxy is disabled or ``PROXY_URL_TIER1`` is absent,
    so callers can skip Tier 0b without conditional logic at the call-site::

        router = build_proxy_router()
        if router:
            pipeline.add_tier(router)

    Args:
        settings: A ``ScrapingSettings``-like object or None (auto-resolved).

    Returns:
        :class:`ProxyRouter` when proxy is enabled and configured, else None.
    """
    if settings is None:
        from searchops.config.settings import get_settings
        settings = get_settings().scraping

    if not getattr(settings, "proxy_enabled", False):
        return None

    proxy_secret = getattr(settings, "proxy_url_tier1", None)
    if proxy_secret is None:
        log.warning(
            "SCRAPING_PROXY_ENABLED=true but PROXY_URL_TIER1 is not set — "
            "Tier 0b disabled; traffic will not be proxied."
        )
        return None

    proxy_url = (
        proxy_secret.get_secret_value()
        if hasattr(proxy_secret, "get_secret_value")
        else str(proxy_secret)
    )

    return ProxyRouter(
        proxy_url=proxy_url,
        impersonate=getattr(settings, "stealth_impersonate", _DEFAULT_IMPERSONATE),
        connect_timeout=getattr(settings, "proxy_connect_timeout", 10.0),
    )


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _mask_proxy(proxy_url: str) -> str:
    """Return *proxy_url* with the password redacted, safe for log output."""
    try:
        parsed = urlparse(proxy_url)
        if parsed.password:
            return proxy_url.replace(parsed.password, "***")
        return proxy_url
    except Exception:
        return "<proxy>"
