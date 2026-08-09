"""
FastAPI application lifespan context manager.

This is the ONLY integration point between FastAPI and the bootstrap layer.
FastAPI's lifespan parameter ensures startup/shutdown are called correctly.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from searchops.bootstrap.startup import startup
from searchops.bootstrap.shutdown import shutdown

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

    log.info("FastAPI application ready")

    # State is available via request.app.state in route handlers
    yield {"container": container}

    # ─── Shutdown ────────────────────────────────────────────────────────────
    await shutdown()
