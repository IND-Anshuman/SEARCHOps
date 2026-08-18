"""
End-to-End browser verification test using Playwright.
Saves verification screenshots to the artifact directory.
"""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
import uvicorn

def _run_server():
    """Background process target to run uvicorn server."""
    os.environ["APP_ENV"] = "testing"
    # Run uvicorn on port 8001
    uvicorn.run("searchops.api.main:app", host="127.0.0.1", port=8001, log_level="info")


@pytest.mark.integration
def test_e2e_operating_console():
    """Starts backend, launches browser via Playwright, and takes a verification screenshot."""
    # 1. Start uvicorn server in a separate process
    server_process = multiprocessing.Process(target=_run_server)
    server_process.start()
    
    # Wait for uvicorn to boot up by dynamically polling the TCP port
    import socket
    booted = False
    start_time = time.time()
    while time.time() - start_time < 30.0:
        try:
            with socket.create_connection(("127.0.0.1", 8001), timeout=1.0):
                booted = True
                break
        except OSError:
            time.sleep(0.5)
            
    if not booted:
        print("Warning: Uvicorn server failed to boot within 30s limit")
    
    try:
        with sync_playwright() as p:
            # Launch headless chromium
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Listen to console messages from the browser
            page.on("console", lambda msg: print(f"BROWSER CONSOLE: [{msg.type}] {msg.text}"))
            
            # Navigate to mounted frontend console
            url = "http://127.0.0.1:8001/"
            print(f"Navigating Playwright to: {url}")
            response = page.goto(url)
            print(f"Page response status: {response.status if response else 'No Response'}")
            print(f"Page title: {page.title()}")
            print(f"Page HTML content:\n{page.content()[:1000]}")
            
            # Wait for console elements to render using auto-waiting selector
            page.wait_for_selector("text=SEARCHOps Console", timeout=15000)
            
            # Verify WS Offline badge is visible since uvicorn runs without active websocket jobs on load
            assert page.locator("text=WS").is_visible()
            
            # Take verification screenshot and save in artifact directory
            screenshot_dir = Path("C:/Users/HP/.gemini/antigravity/brain/0e14cbe7-ba2a-433a-9e87-be176fd98fd6")
            screenshot_path = screenshot_dir / "workspace_e2e_success.png"
            page.screenshot(path=str(screenshot_path))
            print(f"Screenshot successfully saved to: {screenshot_path}")
            
            browser.close()
            
    finally:
        # Shutdown server process
        server_process.terminate()
        server_process.join()
