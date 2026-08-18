"""
Unit tests for application bootstrap container, startup, shutdown, and lifespan.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import FastAPI

from searchops.bootstrap.container import ApplicationContainer, get_container, _set_container
from searchops.bootstrap.startup import startup, get_uptime_seconds
from searchops.bootstrap.shutdown import shutdown
from searchops.bootstrap.lifespan import create_lifespan


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bootstrap_startup_and_container():
    with patch("searchops.bootstrap.startup._verify_redis_connection", new_callable=AsyncMock), \
         patch("searchops.infrastructure.cache.redis.get_redis_client") as mock_redis_cls:
        mock_client = MagicMock()
        mock_client.ping = AsyncMock()
        mock_redis_cls.return_value = mock_client

        container = await startup()
        assert container is not None
        assert get_container() == container
        assert get_uptime_seconds() > 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bootstrap_shutdown_and_lifespan():
    app = FastAPI()
    with patch("searchops.bootstrap.startup._verify_redis_connection", new_callable=AsyncMock), \
         patch("searchops.infrastructure.cache.redis.get_redis_client") as mock_redis_cls:
        mock_client = MagicMock()
        mock_client.ping = AsyncMock()
        mock_client.aclose = AsyncMock()
        mock_redis_cls.return_value = mock_client

        async with create_lifespan(app) as state:
            assert "container" in state
            assert state["container"] is not None

        await shutdown()
