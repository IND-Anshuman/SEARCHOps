"""
Unit tests for ResearchApplicationService lifecycle and execution.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus
from searchops.application.job_state_manager import JobStateManager
from searchops.infrastructure.cache.redis import RedisCache
from searchops.infrastructure.events.bus import RedisEventBus


class MockCache:
    def __init__(self):
        self.store = {}
        self.client = MagicMock()

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: dict, ttl_seconds: int = 3600):
        self.store[key] = value


@pytest.mark.unit
@pytest.mark.asyncio
async def test_research_service_start_and_status():
    mock_cache = MockCache()
    mock_bus = MagicMock(spec=RedisEventBus)
    mock_bus.publish = AsyncMock()
    job_state_manager = JobStateManager(cache=mock_cache, event_bus=mock_bus)

    service = ResearchApplicationService(cache=mock_cache, job_state_manager=job_state_manager)

    job_id = await service.start_research("Quantum Computing", depth="standard", max_sources=3)
    assert job_id is not None

    status = await service.get_job_status(job_id)
    assert status is not None
    assert status["query"] == "Quantum Computing"
    assert status["status"] in (ResearchJobStatus.PENDING, ResearchJobStatus.RUNNING)
