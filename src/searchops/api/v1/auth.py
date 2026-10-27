"""Who is signed in.

Sign-in itself happens in the browser with Clerk; the API only verifies the
session token each request carries (``middleware/auth.py``). This endpoint
lets the workbench check that the backend accepts its session and which user
it resolved to, and tells the page how the server is configured.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from searchops.config.settings import get_settings
from searchops.middleware.auth import AuthResult, authenticate

router = APIRouter(prefix="/auth", tags=["Auth"])


class SessionStatus(BaseModel):
    authenticated: bool
    method: str = ""  # "clerk", "api_key", or "disabled"
    user_id: str = ""
    clerk_enabled: bool = False


@router.get("/session", response_model=SessionStatus)
async def session_status(request: Request) -> SessionStatus:
    """Never 401: the page asks before it knows whether it has a session."""
    settings = get_settings()
    outcome = await authenticate(request.headers, request.cookies, settings)
    principal = outcome.principal
    method = ""
    if outcome.result is AuthResult.DISABLED:
        method = "disabled"
    elif principal is not None:
        method = "clerk" if principal.kind == "user" else "api_key"
    return SessionStatus(
        authenticated=outcome.result in (AuthResult.OK, AuthResult.DISABLED),
        method=method,
        user_id=principal.user_id if principal is not None else "",
        clerk_enabled=settings.security.clerk_enabled,
    )
