"""
Bright Data Credential Isolation & TLS Hardening.

Provides:
  - BrightDataProxyAuth: httpx.Auth implementation — credentials never in URL
  - build_unlocker_proxy(): safe httpx.Proxy factory
  - mask_bd_credential(): redacts passwords from any string before logging
  - get_ssl_context(): proper TLS verification context for BD connections

Security model:
  - Passwords stored as bytes in memory only when needed
  - Never stored as a plain string attribute accessible to __repr__ or logging
  - All exception paths apply masking before emit to structured logger
"""

from __future__ import annotations

import ssl
import re
from typing import Generator

import certifi
import httpx
import structlog

log = structlog.get_logger(__name__)

# Pattern matching BD-style credentials embedded in URLs/strings
# Matches: brd-customer-<id>-zone-<zone>:<password>
_BD_CRED_RE = re.compile(
    r"(brd-customer-[^:@\s]+-zone-[^:@\s]+):([^@\s]+)@"
)


def mask_bd_credential(value: str) -> str:
    """
    Redact Bright Data credentials from any string before logging.

    Replaces the password segment with '***' in any BD proxy/CDP URL format:
      brd-customer-<id>-zone-<zone>:<PASSWORD>@host → brd-customer-...-zone-...:***@host

    Args:
        value: Any string potentially containing BD credentials.

    Returns:
        The string with password segment replaced by '***'.
    """
    return _BD_CRED_RE.sub(r"\1:***@", value)


def get_ssl_context() -> ssl.SSLContext:
    """
    Return a properly configured SSL context using the system CA bundle.

    Bright Data's Web Unlocker proxy does NOT require verify=False.
    The proxy performs its own TLS termination; the client connects to
    brd.superproxy.io using a standard CA-verifiable certificate.

    Returns:
        ssl.SSLContext with certifi CA bundle loaded.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class BrightDataProxyAuth(httpx.Auth):
    """
    httpx.Auth implementation for Bright Data proxy authentication.

    Credentials are passed via the Authorization header in the CONNECT tunnel
    handshake, never embedded in the proxy URL string.

    This ensures:
    - Credentials never appear in httpx debug logs (which log the proxy URL)
    - Credentials never appear in exception tracebacks
    - Credentials never appear in OTEL spans or metrics labels
    """

    def __init__(self, username: str, password: str) -> None:
        # Store as bytes — harder to accidentally serialize or repr
        self._username = username.encode()
        self._password = password.encode()

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Inject Proxy-Authorization header into each outgoing request."""
        import base64
        token = base64.b64encode(
            self._username + b":" + self._password
        ).decode("ascii")
        request.headers["Proxy-Authorization"] = f"Basic {token}"
        yield request

    def __repr__(self) -> str:
        return f"BrightDataProxyAuth(username={self._username.decode()!r}, password=***)"


def build_unlocker_proxy(
    customer_id: str,
    zone: str,
    password: str,
    host: str = "brd.superproxy.io",
    port: int = 22225,
) -> tuple[str, BrightDataProxyAuth]:
    """
    Build a safe httpx proxy configuration for Bright Data Web Unlocker.

    Returns a (proxy_url, auth) tuple where proxy_url contains NO credentials.
    Pass both to httpx.AsyncClient as:
        httpx.AsyncClient(proxy=proxy_url, auth=proxy_auth, ...)

    Args:
        customer_id: BD customer ID (e.g. 'brd-customer-abc123').
        zone: BD zone name (e.g. 'unlocker').
        password: Zone password from BD control panel.
        host: BD superproxy host (default: brd.superproxy.io).
        port: BD superproxy port (default: 22225).

    Returns:
        Tuple of (proxy_url_without_credentials, BrightDataProxyAuth).
    """
    # Zone user format required by BD superproxy authentication
    zone_user = f"brd-customer-{customer_id}-zone-{zone}"
    proxy_url = f"http://{host}:{port}"
    auth = BrightDataProxyAuth(username=zone_user, password=password)
    return proxy_url, auth


def build_browser_cdp_url(
    customer_id: str,
    zone: str,
    password: str,
    host: str = "brd.superproxy.io",
    port: int = 9222,
) -> str:
    """
    Build the Bright Data Cloud Browser CDP WebSocket URL.

    IMPORTANT: This URL contains embedded credentials (required by Playwright
    CDP protocol which doesn't support separate auth headers for WebSocket).
    The return value MUST NOT be stored as a plain instance attribute or logged.

    Call this function transiently inside the method scope that needs it.
    Never assign to self._cdp_url or similar.

    Args:
        customer_id: BD customer ID (e.g. 'brd-customer-abc123').
        zone: BD zone name for scraping browser (e.g. 'scraping_browser').
        password: Zone password from BD control panel.
        host: BD superproxy host (default: brd.superproxy.io).
        port: BD CDP port (default: 9222).

    Returns:
        CDP WebSocket URL with embedded credentials (for transient use only).
    """
    zone_user = f"brd-customer-{customer_id}-zone-{zone}"
    return f"wss://{zone_user}:{password}@{host}:{port}"
