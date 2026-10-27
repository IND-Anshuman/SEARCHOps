"""
FastAPI application lifespan context manager.

This is the ONLY integration point between FastAPI and the bootstrap layer.
FastAPI's lifespan parameter ensures startup/shutdown are called correctly.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog

from searchops.bootstrap.shutdown import shutdown
from searchops.bootstrap.startup import startup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

log = structlog.get_logger(__name__)


@asynccontextmanager
async def create_lifespan(app: FastAPI) -> AsyncIterator[dict[str, object]]:
    """FastAPI lifespan context manager.

    Yields a state dict that is attached to app.state and accessible
    in dependency injectors via request.app.state.

    Args:
        app: The FastAPI application instance.

    Yields:
        dict with the container and other startup artifacts.
    """
    # ─── Startup ─────────────────────────────────────────────────────────────
    container = await startup()
    app.state.container = container
    # Restarts runs a previous process (a redeploy, a crash) left unfinished.
    watchdog = asyncio.create_task(container.research_service.run_watchdog())

    log.info("FastAPI application ready")

    # State is available via request.app.state in route handlers
    yield {"container": container}

    # ─── Shutdown ────────────────────────────────────────────────────────────
    watchdog.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watchdog
    await shutdown()
