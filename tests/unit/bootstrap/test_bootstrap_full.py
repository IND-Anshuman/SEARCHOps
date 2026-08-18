"""
Unit tests for application startup, shutdown, and lifespan.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from searchops.bootstrap.startup import startup, get_uptime_seconds
from searchops.bootstrap.shutdown import shutdown
from searchops.bootstrap.container import ApplicationContainer, get_container


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bootstrap_startup_and_shutdown():
    with patch("searchops.bootstrap.startup.setup_tracer_provider"), patch("searchops.bootstrap.startup.setup_auto_instrumentation"):
        container = await startup()
        assert container is not None
        assert get_container() == container
        assert get_uptime_seconds() >= 0.0

        await shutdown()
