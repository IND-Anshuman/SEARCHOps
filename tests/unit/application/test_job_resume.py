"""Runs survive a restart, and usage caps hold across processes.

A run executes inside one API process and holds a short Redis lease while it
does. These tests pin what happens when that process dies (the lease lapses):
another process's watchdog claims the job exactly once and runs it again; a
job that keeps dying is failed instead of looping; and the per-user caps are
counted in Redis, not in one process's memory.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis as fake_aioredis
import pytest

from searchops.application.job_state_manager import JobStateManager
from searchops.application.research_service import (
    DailyJobLimitExceededError,
    JobLimitExceededError,
    ResearchApplicationService,
)
from searchops.config.settings import get_settings
from searchops.infrastructure.cache.redis import RedisCache

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def redis() -> Any:
    return fake_aioredis.FakeRedis()


def _manager(redis: Any) -> JobStateManager:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return JobStateManager(cache=RedisCache(client=redis), event_bus=bus)


def _service(jsm: JobStateManager) -> tuple[ResearchApplicationService, list[tuple[Any, ...]]]:
    service = ResearchApplicationService(cache=MagicMock(), job_state_manager=jsm)  # type: ignore[arg-type]
    calls: list[tuple[Any, ...]] = []

    async def _record(*args: Any) -> None:
        calls.append(args)

    service._execute_research = _record  # type: ignore[method-assign]
    return service, calls


async def _orphan(redis: Any, jsm: JobStateManager, **state: Any) -> str:
    """A job whose process died: unfinished, lease expired."""
    dead, _ = _service(jsm)
    job_id = await dead.start_research("vector databases", depth="deep", max_sources=7, owner_id="user_a")
    if state:
        await jsm.update_job(job_id, state)
    await redis.delete(f"research:job:{job_id}:lease")
    return job_id


async def test_an_orphaned_run_is_resumed_with_its_own_settings(redis: Any) -> None:
    jsm = _manager(redis)
    job_id = await _orphan(redis, jsm, status="running", progress=40)
    survivor, calls = _service(jsm)

    assert await survivor.resume_interrupted_jobs() == [job_id]
    await asyncio.sleep(0)
    assert calls and calls[0][:4] == (job_id, "vector databases", "deep", 7)
    state = await jsm.get_raw(job_id)
    assert state is not None
    assert state["status"] == "pending" and state["progress"] == 0 and state["resume_count"] == 1
    assert (await redis.get(f"research:job:{job_id}:lease")).decode() == survivor.runner_id


async def test_a_run_whose_process_is_alive_is_left_alone(redis: Any) -> None:
    jsm = _manager(redis)
    owner, _ = _service(jsm)
    await owner.start_research("still running", owner_id="user_a")  # lease held
    other, calls = _service(jsm)
    assert await other.resume_interrupted_jobs() == []
    assert calls == []


async def test_only_one_process_resumes_each_orphan(redis: Any) -> None:
    jsm = _manager(redis)
    job_id = await _orphan(redis, jsm)
    (a, a_calls), (b, b_calls) = _service(jsm), _service(jsm)
    resumed = await asyncio.gather(a.resume_interrupted_jobs(), b.resume_interrupted_jobs())
    await asyncio.sleep(0)
    assert sorted(resumed, key=len) == [[], [job_id]]
    assert len(a_calls) + len(b_calls) == 1


async def test_a_run_that_keeps_dying_is_failed_not_looped(redis: Any) -> None:
    jsm = _manager(redis)
    job_id = await _orphan(redis, jsm, resume_count=1)
    survivor, calls = _service(jsm)
    assert await survivor.resume_interrupted_jobs() == []
    state = await jsm.get_raw(job_id)
    assert state is not None and state["status"] == "failed"
    assert calls == []
    assert job_id not in await jsm.unfinished_job_ids()


async def test_finished_jobs_leave_the_unfinished_set(redis: Any) -> None:
    jsm = _manager(redis)
    service, _ = _service(jsm)
    job_id = await service.start_research("q", owner_id="user_a")
    assert job_id in await jsm.unfinished_job_ids()
    await jsm.update_job(job_id, {"status": "completed"})
    assert job_id not in await jsm.unfinished_job_ids()


async def test_a_run_holds_its_lease_while_running_and_releases_it_after(redis: Any) -> None:
    jsm = _manager(redis)
    service = ResearchApplicationService(cache=MagicMock(), job_state_manager=jsm)  # type: ignore[arg-type]
    seen: list[str] = []

    async def _run(job_id: str, *_: Any) -> None:
        await asyncio.sleep(0.01)
        seen.append((await redis.get(f"research:job:{job_id}:lease")).decode())

    service._run_research = _run  # type: ignore[method-assign]
    from searchops.core.context.execution import ExecutionContext

    await service._execute_research("job-1", "q", "standard", 5, ExecutionContext.create())
    assert seen == [service.runner_id]
    assert await redis.get("research:job:job-1:lease") is None


# ── Usage caps ───────────────────────────────────────────────────────────────


async def test_the_concurrent_cap_counts_runs_started_by_other_processes(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_MAX_CONCURRENT_JOBS_PER_USER", "1")
    get_settings.cache_clear()
    jsm = _manager(redis)
    first_process, _ = _service(jsm)
    second_process, _ = _service(jsm)
    await first_process.start_research("q1", owner_id="user_a")
    with pytest.raises(JobLimitExceededError):
        await second_process.start_research("q2", owner_id="user_a")
    await second_process.start_research("someone else", owner_id="user_b")
    get_settings.cache_clear()


async def test_the_daily_cap_stops_further_runs(redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_MAX_CONCURRENT_JOBS_PER_USER", "0")
    monkeypatch.setenv("API_MAX_JOBS_PER_USER_PER_DAY", "2")
    get_settings.cache_clear()
    jsm = _manager(redis)
    service, _ = _service(jsm)
    await service.start_research("q1", owner_id="user_a")
    await service.start_research("q2", owner_id="user_a")
    with pytest.raises(DailyJobLimitExceededError):
        await service.start_research("q3", owner_id="user_a")
    await service.start_research("service callers are exempt")
    get_settings.cache_clear()


async def test_the_status_response_reports_a_resume() -> None:
    from searchops.api.v1.research import JobStatusResponse

    body = JobStatusResponse.model_validate(
        {"job_id": "j", "status": "running", "resume_count": 1, "resumed_at": "2026-10-26T17:19:05+00:00"}
    ).model_dump()
    assert body["resume_count"] == 1 and body["resumed_at"].startswith("2026-10-26")
