"""
Unit tests for JobStateManager.

Uses fakeredis to run a real Redis-protocol server in-process.
No mocks of JobStateManager itself — the real implementation is tested.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import fakeredis.aioredis as fake_aioredis
import pytest

from searchops.application.job_state_manager import JobStateManager
from searchops.infrastructure.cache.redis import RedisCache
from searchops.infrastructure.events.bus import RedisEventBus


@pytest.fixture
async def fake_redis():
    """Provide a fakeredis async client that supports pub/sub."""
    server = fake_aioredis.FakeRedis()
    yield server
    await server.aclose()


@pytest.fixture
async def job_state_manager(fake_redis):
    """Provide a JobStateManager backed by fakeredis."""
    cache = RedisCache(client=fake_redis)
    bus = RedisEventBus(cache=cache)
    return JobStateManager(cache=cache, event_bus=bus)


# ── create_job ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_job_persists_state(job_state_manager: JobStateManager):
    """create_job should persist job state and make it retrievable via get_job."""
    await job_state_manager.create_job("job-001", {
        "status": "pending",
        "query": "test query",
        "progress": 0,
    })

    state = await job_state_manager.get_job("job-001")
    assert state is not None
    assert state["job_id"] == "job-001"
    assert state["status"] == "pending"
    assert state["query"] == "test query"


@pytest.mark.asyncio
async def test_create_job_sets_job_id(job_state_manager: JobStateManager):
    """create_job should inject job_id into the state dict even if not provided."""
    await job_state_manager.create_job("job-002", {"status": "pending"})
    state = await job_state_manager.get_job("job-002")
    assert state["job_id"] == "job-002"


# ── update_job ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_job_merges_patch(job_state_manager: JobStateManager):
    """update_job should merge new fields without overwriting unrelated existing fields."""
    await job_state_manager.create_job("job-003", {
        "status": "pending",
        "query": "original query",
        "progress": 0,
    })

    merged = await job_state_manager.update_job("job-003", {
        "status": "running",
        "progress": 25,
    })

    assert merged["status"] == "running"
    assert merged["progress"] == 25
    # Original field preserved
    assert merged["query"] == "original query"


@pytest.mark.asyncio
async def test_update_job_persists_to_redis(job_state_manager: JobStateManager):
    """update_job result should be retrievable via get_job."""
    await job_state_manager.create_job("job-004", {"status": "pending"})
    await job_state_manager.update_job("job-004", {"status": "running", "progress": 50})

    state = await job_state_manager.get_job("job-004")
    assert state["status"] == "running"
    assert state["progress"] == 50


# ── get_job / get_or_replay ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_job_returns_none_for_unknown(job_state_manager: JobStateManager):
    """get_job should return None for a job that was never created."""
    state = await job_state_manager.get_job("nonexistent-job")
    assert state is None


@pytest.mark.asyncio
async def test_get_or_replay_returns_latest_state(job_state_manager: JobStateManager):
    """get_or_replay should return the current persisted state for replay to late subscribers."""
    await job_state_manager.create_job("job-005", {"status": "completed", "progress": 100})
    state = await job_state_manager.get_or_replay("job-005")
    assert state is not None
    assert state["status"] == "completed"


# ── delete_job ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_job_removes_state(job_state_manager: JobStateManager):
    """delete_job should remove the job from Redis."""
    await job_state_manager.create_job("job-006", {"status": "pending"})
    await job_state_manager.delete_job("job-006")
    state = await job_state_manager.get_job("job-006")
    assert state is None


# ── subscribe (pub/sub) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscribe_receives_updates():
    """subscribe() should yield state snapshots published by update_job()."""
    # fakeredis pub/sub requires a real server instance for proper pub/sub support
    # We test the pub/sub pipeline with two separate client connections
    server = fake_aioredis.FakeServer()
    client_a = fake_aioredis.FakeRedis(server=server)
    client_b = fake_aioredis.FakeRedis(server=server)

    cache_a = RedisCache(client=client_a)
    bus_a = RedisEventBus(cache=cache_a)
    manager_a = JobStateManager(cache=cache_a, event_bus=bus_a)

    cache_b = RedisCache(client=client_b)
    bus_b = RedisEventBus(cache=cache_b)
    manager_b = JobStateManager(cache=cache_b, event_bus=bus_b)

    received: list[dict] = []

    async def subscribe_and_collect():
        async for state in manager_b.subscribe("job-007"):
            received.append(state)
            if state.get("status") == "completed":
                break

    # Start subscriber in background
    sub_task = asyncio.create_task(subscribe_and_collect())
    await asyncio.sleep(0.05)  # Let subscriber connect

    # Publish updates from writer
    await manager_a.create_job("job-007", {"status": "running", "progress": 50})
    await asyncio.sleep(0.05)
    await manager_a.update_job("job-007", {"status": "completed", "progress": 100})

    try:
        await asyncio.wait_for(sub_task, timeout=3.0)
    except asyncio.TimeoutError:
        sub_task.cancel()
        pytest.fail("subscribe() timed out — no messages received")

    assert len(received) >= 1
    terminal = [r for r in received if r.get("status") == "completed"]
    assert len(terminal) == 1, f"Expected at least one completed state, got: {received}"

    await client_a.aclose()
    await client_b.aclose()
