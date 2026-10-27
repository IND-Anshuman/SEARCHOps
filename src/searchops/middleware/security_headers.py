"""
Baseline security response headers.

Applied to every response, including the SPA's static files, together with a
``Content-Security-Policy`` built for this app: its own origin, Google Fonts
(``index.html`` loads the type faces from there) and, when Clerk is
configured, the hosts Clerk's documentation lists for sign-in — the
instance's Frontend API (read from the publishable key), Cloudflare's bot
check and Clerk's fraud-protection hosts. ``CONTENT_SECURITY_POLICY``
replaces the built policy outright.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware

from searchops.config.settings import get_settings
from searchops.middleware.clerk import ClerkConfigError, frontend_api_from_publishable_key

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

    from searchops.config.subsystems.security import SecuritySettings

log = structlog.get_logger(__name__)

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",  # Clerk's OAuth popups need this
}
_HSTS = "max-age=31536000; includeSubDomains"


def build_content_security_policy(security: SecuritySettings) -> str:
    """The policy for this app; Clerk's hosts only when Clerk is configured."""
    if security.content_security_policy.strip():
        return security.content_security_policy.strip()

    clerk_script = clerk_connect = clerk_frame = clerk_img = ""
    if security.clerk_enabled:
        try:
            fapi = "https://" + frontend_api_from_publishable_key(security.clerk_publishable_key or "")
        except ClerkConfigError as exc:
            log.warning("csp.clerk_host_unknown", error=str(exc))
            fapi = ""
        protect = "https://*.protect.clerk.com"
        clerk_script = f" {fapi} https://challenges.cloudflare.com {protect}"
        # The fraud-protection hosts serve on ports other than 443; ":*" is required.
        clerk_connect = f" {fapi} {protect}:* https://clerk-telemetry.com https://*.clerk-telemetry.com"
        clerk_frame = f" https://challenges.cloudflare.com {protect}"
        clerk_img = " https://img.clerk.com"

    directives = [
        "default-src 'self'",
        # 'unsafe-inline' for scripts and styles: Clerk injects both at runtime.
        f"script-src 'self' 'unsafe-inline'{clerk_script}",
        # 'self' covers the app's own WebSocket in current browsers.
        f"connect-src 'self'{clerk_connect}",
        f"img-src 'self' data:{clerk_img}",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "worker-src 'self' blob:",
        f"frame-src 'self'{clerk_frame}",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
    return "; ".join(" ".join(d.split()) for d in directives)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds the headers above; HSTS only when the request arrived over HTTPS."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in _HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Content-Security-Policy", build_content_security_policy(get_settings().security))
        forwarded = request.headers.get("x-forwarded-proto", "")
        if request.url.scheme == "https" or forwarded == "https":
            response.headers.setdefault("Strict-Transport-Security", _HSTS)
        return response
