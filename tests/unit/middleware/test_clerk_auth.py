"""Clerk sign-in, WebSocket auth, job ownership and refusal of known keys.

Clerk session tokens are minted here with a locally generated RSA key and
served through a patched JWKS fetch, so the full verification path (signature,
issuer, expiry, authorized party) runs without the network.
"""

from __future__ import annotations

import base64
import functools
import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from searchops.api.v1.auth import router as auth_router
from searchops.api.v1.websocket import router as ws_router
from searchops.config.settings import get_settings
from searchops.config.subsystems.security import INSECURE_DEFAULT_SECRET_KEY
from searchops.middleware.auth import APIKeyAuthMiddleware, Principal, can_access_job, key_matches
from searchops.middleware.clerk import ClerkConfigError, frontend_api_from_publishable_key, get_clerk_verifier

pytestmark = pytest.mark.unit

KEY = "s3cret-" + "x" * 32
ORIGIN = "http://localhost:3000"
HOST = "test-instance-1.clerk.accounts.dev"
ISSUER = f"https://{HOST}"
PUBLISHABLE_KEY = "pk_test_" + base64.b64encode(f"{HOST}$".encode()).decode().rstrip("=")
KID = "ins_test_key"

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_SIGNING_KEY.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": KID, "use": "sig", "alg": "RS256"}]}


def _token(
    sub: str = "user_alice",
    *,
    iss: str = ISSUER,
    azp: str = ORIGIN,
    exp_in: int = 60,
    key: Any = None,
    **extra: Any,
) -> str:
    now = int(time.time())
    claims = {"sub": sub, "sid": "sess_1", "iss": iss, "azp": azp, "iat": now, "nbf": now, "exp": now + exp_in}
    claims.update(extra)
    return jwt.encode(claims, key or _SIGNING_KEY, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(autouse=True)
def _offline_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: _jwks())
    get_clerk_verifier.cache_clear()


def _settings(monkeypatch: pytest.MonkeyPatch, *, api_key: str = KEY, clerk: bool = True, secret: str = KEY) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", secret)
    monkeypatch.setenv("SEARCHOPS_API_KEY", api_key)
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", PUBLISHABLE_KEY if clerk else "")
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "")
    monkeypatch.delenv("AUTH_DISABLED_EXPLICITLY", raising=False)
    monkeypatch.setenv("API_ALLOWED_ORIGINS", f'["{ORIGIN}"]')
    get_settings.cache_clear()
    fresh = functools.lru_cache(maxsize=1)(lambda: get_settings())
    for target in ("searchops.middleware.auth", "searchops.api.v1.auth", "searchops.api.v1.websocket"):
        monkeypatch.setattr(f"{target}.get_settings", fresh)


class _Jobs:
    """One completed job, owned by alice."""

    async def get_or_replay(self, job_id: str) -> dict[str, Any] | None:
        if job_id != "job-1":
            return None
        return {"job_id": job_id, "status": "completed", "owner_id": "user_alice"}


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(ws_router)

    @app.get("/api/v1/data")
    async def data() -> dict[str, bool]:
        return {"ok": True}

    app.state.container = SimpleNamespace(job_state_manager=_Jobs())
    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Configuration ────────────────────────────────────────────────────────────


def test_publishable_key_decodes_to_frontend_api_host() -> None:
    assert frontend_api_from_publishable_key(PUBLISHABLE_KEY) == HOST
    for bad in ("sk_test_abc", "pk_test_", "pk_dev_" + PUBLISHABLE_KEY[8:], "pk_test_%%%"):
        with pytest.raises(ClerkConfigError):
            frontend_api_from_publishable_key(bad)


@pytest.mark.parametrize("secret", [INSECURE_DEFAULT_SECRET_KEY, "CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING", "short"])
def test_publicly_known_keys_never_authenticate(monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    _settings(monkeypatch, api_key="", clerk=False, secret=secret)
    res = TestClient(_app()).get("/api/v1/data", headers={"X-API-Key": secret})
    assert res.status_code == 500
    assert "not configured" in res.json()["detail"]


def test_key_comparison() -> None:
    assert key_matches(KEY, KEY)
    assert not key_matches("", "")
    assert not key_matches(KEY[:-1], KEY)


# ── Clerk session tokens ─────────────────────────────────────────────────────


def test_clerk_token_authenticates_and_identifies_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, api_key="")  # Clerk alone is a complete configuration
    client = TestClient(_app())
    assert client.get("/api/v1/data").status_code == 401
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False

    token = _token()
    assert client.get("/api/v1/data", headers=_bearer(token)).status_code == 200
    session = client.get("/api/v1/auth/session", headers=_bearer(token)).json()
    assert session == {"authenticated": True, "method": "clerk", "user_id": "user_alice", "clerk_enabled": True}


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: _token(exp_in=-120), id="expired"),
        pytest.param(lambda: _token(iss="https://other.clerk.accounts.dev"), id="wrong-issuer"),
        pytest.param(lambda: _token(key=_OTHER_KEY), id="forged-signature"),
        pytest.param(lambda: _token(azp="https://evil.example"), id="foreign-authorized-party"),
        pytest.param(lambda: _token(sts="pending"), id="pending-session"),
    ],
)
def test_invalid_clerk_tokens_are_refused(monkeypatch: pytest.MonkeyPatch, token: Any) -> None:
    _settings(monkeypatch)
    assert TestClient(_app()).get("/api/v1/data", headers=_bearer(token())).status_code == 401


def test_clerk_session_cookie_authenticates_same_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the SPA is served by the API itself, Clerk's own __session cookie is enough."""
    _settings(monkeypatch)
    client = TestClient(_app())
    client.cookies.set("__session", _token())
    assert client.get("/api/v1/data").status_code == 200


def test_api_key_still_works_for_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    client = TestClient(_app())
    assert client.get("/api/v1/data", headers={"X-API-Key": KEY}).status_code == 200
    assert client.get("/api/v1/auth/session", headers={"X-API-Key": KEY}).json()["method"] == "api_key"
    assert client.get("/api/v1/data", headers={"X-API-Key": "nope-" + "y" * 30}).status_code == 401


# ── WebSocket ────────────────────────────────────────────────────────────────


def _ws_close_code(client: TestClient, job: str = "job-1", **kw: Any) -> int:
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(f"/ws/research/{job}", **kw) as ws:
        ws.receive_text()
        ws.receive_text()
    return exc.value.code


def test_websocket_without_credentials_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    assert _ws_close_code(TestClient(_app())) == 4401


def test_websocket_accepts_clerk_token_as_subprotocol_without_echoing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    client = TestClient(_app())
    with client.websocket_connect(
        "/ws/research/job-1", subprotocols=["searchops", f"bearer.{_token()}"], headers={"Origin": ORIGIN}
    ) as ws:
        assert ws.accepted_subprotocol == "searchops"
        assert ws.receive_json()["status"] == "completed"


def test_websocket_accepts_api_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    with TestClient(_app()).websocket_connect("/ws/research/job-1", headers={"X-API-Key": KEY}) as ws:
        assert ws.receive_json()["job_id"] == "job-1"


def test_websocket_from_a_foreign_page_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    client = TestClient(_app())
    code = _ws_close_code(
        client, subprotocols=["searchops", f"bearer.{_token()}"], headers={"Origin": "https://evil.example"}
    )
    assert code == 4403


# ── Ownership ────────────────────────────────────────────────────────────────


def test_a_user_cannot_stream_another_users_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    client = TestClient(_app())
    code = _ws_close_code(
        client, subprotocols=["searchops", f"bearer.{_token('user_bob')}"], headers={"Origin": ORIGIN}
    )
    assert code == 4404  # indistinguishable from a missing job


def test_job_visibility_rules() -> None:
    alice, bob, service = Principal("user", "user_alice"), Principal("user", "user_bob"), Principal("service")
    owned = {"owner_id": "user_alice"}
    assert can_access_job(owned, alice)
    assert not can_access_job(owned, bob)
    assert can_access_job(owned, service)
    assert can_access_job({"owner_id": ""}, bob)  # jobs from before ownership stay visible
