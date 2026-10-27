"""
API authentication.

Two kinds of caller:

- **People** sign in to the workbench with Clerk. The browser sends its Clerk
  session token as ``Authorization: Bearer <jwt>`` (HTTP), as the WebSocket
  subprotocol ``bearer.<jwt>`` (browsers cannot set headers on a socket), or —
  when the SPA is served from the API's own origin — as Clerk's ``__session``
  cookie. The token is verified against the Clerk instance's JWKS
  (``middleware/clerk.py``) and yields a user principal.
- **Services and scripts** send ``X-API-Key: <SEARCHOPS_API_KEY>`` (or
  ``Authorization: Bearer <key>``) and get a service principal, which may
  read every job.

The authenticated principal is published in a context variable so job
endpoints can restrict a user to the reports they started.

``authenticate`` is shared by the HTTP middleware and the WebSocket endpoint:
``BaseHTTPMiddleware`` never sees WebSocket connections, so the socket has to
check for itself.
"""

from __future__ import annotations

import hmac
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from searchops.config.settings import get_settings
from searchops.config.subsystems.security import INSECURE_DEFAULT_SECRET_KEY
from searchops.middleware.clerk import ClerkConfigError, ClerkTokenError, get_clerk_verifier, looks_like_jwt

if TYPE_CHECKING:
    from collections.abc import Mapping

    from starlette.requests import Request

    from searchops.config.settings import Settings
    from searchops.config.subsystems.security import SecuritySettings

log = structlog.get_logger(__name__)

CLERK_SESSION_COOKIE = "__session"  # set by Clerk on the app's own domain
WS_BEARER_PREFIX = "bearer."  # WebSocket subprotocol carrying the session token
_MIN_KEY_LENGTH = 16
# Reachable without credentials: the page asks whether it is signed in.
PUBLIC_API_PATHS = frozenset({"/api/v1/auth/session"})
# Operator surfaces: reachable by services (API key) only, never by a signed-in
# end user. Metrics sit outside /api/v1 but must not be public.
SERVICE_ONLY_PATHS = frozenset({"/metrics"})


class AuthResult(Enum):
    OK = "ok"
    DISABLED = "disabled"  # explicit opt-out; the caller logs it
    UNAUTHORIZED = "unauthorized"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class Principal:
    """Who is making the request."""

    kind: str  # "user" (Clerk), "service" (API key), or "anonymous" (auth disabled)
    user_id: str = ""

    @property
    def sees_all_jobs(self) -> bool:
        return self.kind != "user"


@dataclass(frozen=True)
class AuthOutcome:
    result: AuthResult
    principal: Principal | None = None


_current_principal: ContextVar[Principal | None] = ContextVar("searchops_principal", default=None)


def current_principal() -> Principal | None:
    """The authenticated caller of the current request, if any."""
    return _current_principal.get()


def set_current_principal(principal: Principal | None) -> None:
    _current_principal.set(principal)


def can_access_job(job: Mapping[str, Any] | None, principal: Principal | None = None) -> bool:
    """A user sees the jobs they started; services see everything.

    Jobs created before ownership was recorded have no owner and stay visible.
    Background execution runs with no principal and is never restricted.
    """
    who = principal if principal is not None else current_principal()
    if job is None or who is None or who.sees_all_jobs:
        return True
    owner = str(job.get("owner_id") or "")
    return not owner or owner == who.user_id


def configured_key(security: SecuritySettings) -> str:
    """The API key requests are checked against, or "" when none is usable.

    ``api_key`` wins when set, so it can be rotated without touching
    ``secret_key``. A key anyone can know never counts as configured: the
    built-in default (published in the source), the ``.env.example``
    placeholder copied verbatim, or anything short enough to guess.
    """
    raw = security.api_key or security.secret_key
    key = raw.get_secret_value() if raw is not None else ""
    if key == INSECURE_DEFAULT_SECRET_KEY or key.upper().startswith("CHANGE_ME") or len(key) < _MIN_KEY_LENGTH:
        return ""
    return key


def key_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison, so response timing leaks nothing about the key."""
    return bool(provided and expected) and hmac.compare_digest(provided.encode(), expected.encode())


def _authorized_parties(settings: Settings) -> tuple[str, ...]:
    parties = settings.security.clerk_authorized_parties or list(settings.api.allowed_origins)
    return tuple(p.rstrip("/") for p in parties if p and p != "*")


async def _verify_clerk(token: str, settings: Settings) -> Principal | None:
    security = settings.security
    if not security.clerk_enabled:
        return None
    try:
        verifier = get_clerk_verifier(
            str(security.clerk_publishable_key), _authorized_parties(settings), security.clerk_jwks_url
        )
        session = await verifier.verify_async(token)
    except ClerkConfigError as exc:
        log.error("auth.clerk.misconfigured", error=str(exc))
        return None
    except ClerkTokenError as exc:
        log.info("auth.clerk.token_rejected", reason=str(exc))
        return None
    return Principal(kind="user", user_id=session.user_id)


async def authenticate(
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
    settings: Settings,
    *,
    bearer_token: str = "",
) -> AuthOutcome:
    """Resolve the caller from an API key or a Clerk session token."""
    security = settings.security
    if security.auth_disabled_explicitly:
        return AuthOutcome(AuthResult.DISABLED, Principal(kind="anonymous"))
    api_key = configured_key(security)
    if not api_key and not security.clerk_enabled:
        return AuthOutcome(AuthResult.UNCONFIGURED)

    provided_key = headers.get("x-api-key") or ""
    auth_header = headers.get("authorization") or ""
    bearer = bearer_token or (auth_header[7:].strip() if auth_header.startswith("Bearer ") else "")

    if api_key and (key_matches(provided_key, api_key) or key_matches(bearer, api_key)):
        return AuthOutcome(AuthResult.OK, Principal(kind="service"))

    for token in (bearer, cookies.get(CLERK_SESSION_COOKIE) or ""):
        if token and looks_like_jwt(token):
            principal = await _verify_clerk(token, settings)
            if principal is not None:
                return AuthOutcome(AuthResult.OK, principal)
    return AuthOutcome(AuthResult.UNAUTHORIZED)


def bearer_from_subprotocols(header: str | None) -> tuple[str, str | None]:
    """Pull a Clerk token out of ``Sec-WebSocket-Protocol``.

    The client offers ``["searchops", "bearer.<jwt>"]``; the server must echo
    one offered protocol back, and echoes ``searchops`` so the token is never
    reflected. Returns ``(token, protocol_to_accept)``.
    """
    offered = [p.strip() for p in (header or "").split(",") if p.strip()]
    token = next((p[len(WS_BEARER_PREFIX) :] for p in offered if p.startswith(WS_BEARER_PREFIX)), "")
    accept = "searchops" if "searchops" in offered else None
    return token, accept


def origin_allowed(origin: str | None, host: str | None, settings: Settings) -> bool:
    """Block cross-site WebSocket hijacking: a browser sends cookies with a
    socket opened from any page, so the page's origin must be ours.
    Non-browser clients send no Origin and authenticate with a header instead."""
    if not origin:
        return True
    if origin in settings.api.allowed_origins:
        return True
    return bool(host) and urlsplit(origin).netloc == host


UNCONFIGURED_DETAIL = (
    "Server authentication is not configured. Set CLERK_PUBLISHABLE_KEY for user sign-in "
    "and/or SEARCHOPS_API_KEY (a random value) for scripts."
)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Enforces Clerk session or API-key auth on the API and socket surfaces."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Exclude CORS preflight and anything outside the protected surfaces.
        # Deliberately NOT excluded: the environment. Disabling auth requires
        # the explicit AUTH_DISABLED_EXPLICITLY opt-in.
        if request.method == "OPTIONS" or path in PUBLIC_API_PATHS:
            return await call_next(request)
        if not (path.startswith(("/api/v1", "/ws")) or path in SERVICE_ONLY_PATHS):
            return await call_next(request)

        outcome = await authenticate(request.headers, request.cookies, get_settings())
        if outcome.result is AuthResult.DISABLED:
            log.warning(
                "auth.middleware.disabled_explicitly",
                path=path,
                hint="AUTH_DISABLED_EXPLICITLY is set; API is unauthenticated",
            )
        elif outcome.result is AuthResult.UNCONFIGURED:
            # Fail closed: missing auth config is a deployment mistake, not an invitation.
            log.error("auth.middleware.not_configured", path=path)
            return JSONResponse(status_code=500, content={"detail": UNCONFIGURED_DETAIL})
        elif outcome.result is AuthResult.UNAUTHORIZED:
            log.warning(
                "Unauthorized API access attempt",
                path=path,
                client_ip=request.client.host if request.client else "unknown",
            )
            return JSONResponse(status_code=401, content={"detail": "Sign in to continue."})

        if path in SERVICE_ONLY_PATHS and outcome.principal is not None and not outcome.principal.sees_all_jobs:
            return JSONResponse(status_code=403, content={"detail": "This endpoint is for service accounts."})

        request.state.principal = outcome.principal
        set_current_principal(outcome.principal)
        return await call_next(request)


def visible_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    """The job if the current caller may see it, else ``None`` (reported as 404,
    so another user's report is indistinguishable from a missing one)."""
    return job if can_access_job(job) else None


async def require_service_principal() -> None:
    """FastAPI dependency for operator endpoints (tool execution, agent
    registration, dead-letter inspection): a Clerk end user is refused, because
    these spend provider credits or accept arbitrary remote endpoints."""
    from fastapi import HTTPException, status

    principal = current_principal()
    if principal is not None and not principal.sees_all_jobs:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This endpoint is for service accounts.")
