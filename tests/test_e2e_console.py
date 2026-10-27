"""
End-to-end check of the deployed shape: the API serving the built web app.

Starts uvicorn on :8001 against ``frontend/dist`` and opens it in headless
Chromium. It verifies what only a real browser can:

- the app boots to Clerk's sign-in screen (Clerk's script loads and renders);
- the Content-Security-Policy the API sends blocks nothing the page needs —
  a policy that is wrong for Clerk breaks sign-in silently, with only a
  console error to show for it.

Needs: a build of the web app (``npm --prefix frontend run build`` with
``VITE_API_BASE_URL=""``), ``CLERK_PUBLISHABLE_KEY`` in ``.env``, Redis, and
network access to the Clerk instance. Skipped when the build is missing.
"""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import httpx
import pytest

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
BASE = "http://localhost:8001"


def _run_server() -> None:
    import uvicorn

    os.environ["APP_ENV"] = "testing"
    # conftest blanks these for unit tests; this server must use the real .env
    # values, or the policy it sends omits Clerk and the browser blocks sign-in.
    for name in ("CLERK_PUBLISHABLE_KEY", "SEARCHOPS_API_KEY", "REDIS_PASSWORD"):
        os.environ.pop(name, None)
    uvicorn.run("searchops.api.main:app", host="127.0.0.1", port=8001, log_level="warning")


def _wait_until_live(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/health/live", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError("API did not come up on :8001")


@pytest.mark.integration
@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="frontend/dist not built")
def test_app_boots_to_sign_in_with_no_csp_violations(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    server = multiprocessing.Process(target=_run_server, daemon=True)
    server.start()
    try:
        _wait_until_live()
        violations: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on(
                "console",
                lambda msg: violations.append(msg.text) if "Content Security Policy" in msg.text else None,
            )
            response = page.goto(f"{BASE}/")
            assert response is not None and response.ok
            assert "content-security-policy" in response.headers

            page.wait_for_selector("text=Sign in to run research", timeout=30_000)
            # Clerk's own sign-in form, rendered by the script the policy must allow.
            page.wait_for_selector("input[name='identifier']", timeout=30_000)
            page.screenshot(path=str(tmp_path / "sign_in.png"))
            browser.close()
        assert violations == [], violations
    finally:
        server.terminate()
        server.join(timeout=10)
