"""
Clerk session verification.

The browser signs in with Clerk and sends its short-lived session token (a
JWT, ~60 s lifetime, refreshed by the Clerk SDK) as ``Authorization: Bearer``.
This module verifies that token without Clerk's secret key:

- The publishable key encodes the instance's Frontend API host
  (``pk_test_<base64("charming-hound-9662.clerk.accounts.dev$")>``). The token
  issuer must be ``https://<that host>`` and its signing keys are published at
  ``https://<that host>/.well-known/jwks.json``.
- The signature is checked against that JWKS (RS256), and ``exp``/``nbf``/
  ``iss`` are enforced. ``azp`` — the origin the token was minted for — must
  be one of the authorized parties, so a token stolen from another site that
  uses the same Clerk instance cannot be replayed here.

JWKS keys are cached in-process; a fetch happens once, then only on key
rotation.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
import structlog

log = structlog.get_logger(__name__)

_LEEWAY_SECONDS = 10  # clock skew between this server and Clerk


class ClerkConfigError(ValueError):
    """The publishable key cannot be decoded into a Frontend API host."""


class ClerkTokenError(Exception):
    """A presented session token is not valid."""


@dataclass(frozen=True)
class ClerkSession:
    user_id: str
    session_id: str
    claims: dict[str, Any]


def frontend_api_from_publishable_key(publishable_key: str) -> str:
    """``pk_(test|live)_<base64(host$)>`` → ``host``."""
    prefix, _, encoded = publishable_key.strip().partition("_")
    env, _, encoded = encoded.partition("_")
    if prefix != "pk" or env not in ("test", "live") or not encoded:
        raise ClerkConfigError("CLERK_PUBLISHABLE_KEY must look like pk_test_... or pk_live_...")
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ClerkConfigError("CLERK_PUBLISHABLE_KEY is not valid base64") from exc
    host = decoded.rstrip("$").strip()
    if not host or "/" in host or " " in host:
        raise ClerkConfigError("CLERK_PUBLISHABLE_KEY does not encode a Frontend API host")
    return host


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and token.startswith("ey")


class ClerkVerifier:
    """Verifies Clerk session tokens for one Clerk instance."""

    def __init__(self, publishable_key: str, authorized_parties: tuple[str, ...], jwks_url: str | None = None) -> None:
        host = frontend_api_from_publishable_key(publishable_key)
        self.issuer = f"https://{host}"
        self.jwks_url = jwks_url or f"{self.issuer}/.well-known/jwks.json"
        self.authorized_parties = tuple(p.rstrip("/") for p in authorized_parties if p)
        self._jwks = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600, timeout=10)

    def verify(self, token: str) -> ClerkSession:
        """Verify *token* synchronously (may fetch the JWKS on first use)."""
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except jwt.PyJWKClientError as exc:
            # Unknown key id or JWKS unreachable: the token cannot be checked,
            # so it is refused (fail closed), and the reason is logged.
            log.warning("auth.clerk.jwks_unavailable", error=str(exc))
            raise ClerkTokenError("signing key unavailable") from exc
        except jwt.InvalidTokenError as exc:
            raise ClerkTokenError(str(exc)) from exc

        azp = str(claims.get("azp") or "").rstrip("/")
        if azp and self.authorized_parties and azp not in self.authorized_parties:
            raise ClerkTokenError(f"token minted for an unauthorized party: {azp}")
        if claims.get("sts") == "pending":
            # Session exists but still owes a step (e.g. a required task).
            raise ClerkTokenError("session is pending")
        return ClerkSession(user_id=str(claims["sub"]), session_id=str(claims.get("sid") or ""), claims=claims)

    async def verify_async(self, token: str) -> ClerkSession:
        """Verify off the event loop: a cold JWKS fetch is a blocking HTTP call."""
        return await asyncio.to_thread(self.verify, token)


@lru_cache(maxsize=4)
def get_clerk_verifier(
    publishable_key: str, authorized_parties: tuple[str, ...], jwks_url: str | None
) -> ClerkVerifier:
    """One verifier (and JWKS cache) per configuration."""
    return ClerkVerifier(publishable_key, authorized_parties, jwks_url)
