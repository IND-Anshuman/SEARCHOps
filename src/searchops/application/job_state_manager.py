"""
Job State Manager.

The single authoritative component responsible for the full lifecycle of a
research job. No REST handler or WebSocket handler owns state directly.

Responsibilities:
  - Job creation and initial persistence (Redis with TTL)
  - Atomic state updates (patch merge + pub/sub notification)
  - State retrieval for REST polling
  - Pub/Sub subscription for real-time WebSocket streaming
  - Late-subscriber replay (connect after job completed → get final state)
  - Job cancellation

Redis key space:
  - research:job:{job_id}          → orjson-serialized job state dict (TTL: 1h
                                     while running, 7 days once finished)
  - searchops:job:{job_id}:events  → Redis pub/sub channel for job updates
  - research:jobs:unfinished        → set of job ids not yet finished
  - research:job:{job_id}:lease     → id of the process running the job (short
                                     TTL, refreshed while it runs)

Leases and restarts:
  A run executes inside one API process. While it runs, that process refreshes
  the job's lease every few seconds. If the process dies (a redeploy, a crash),
  the lease expires; any process's watchdog then finds the unfinished job
  without a lease, claims it, and runs it again (see
  ``ResearchApplicationService.resume_interrupted_jobs``).

Durable archive:
  A finished job (completed / failed) is also written to
  ``$SEARCHOPS_REPORT_DIR/{job_id}.json`` (default ``./data/reports``). Reads
  fall back to it, so a report survives Redis eviction and server restarts.

Design:
  Each `update_job()` call atomically:
    1. Reads current state from Redis
    2. Merges the patch dict into it
    3. Writes back with refreshed TTL
    4. Publishes the merged state to the pub/sub channel

  `subscribe(job_id)` opens a Redis pub/sub subscription and yields
  decoded state snapshots as they are published. This is event-driven —
  no polling loops.

  `get_or_replay(job_id)` returns the current persisted state immediately.
  WebSocket handlers call this first to handle late subscribers:
  if the job is already completed, the client gets the final state at once.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from searchops.shared.json_safe import json_safe

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from searchops.infrastructure.cache.redis import RedisCache
    from searchops.infrastructure.events.bus import RedisEventBus

log = structlog.get_logger(__name__)

_JOB_TTL = 3600  # 1 hour
_FINISHED_JOB_TTL = 7 * 24 * 3600  # finished reports stay hot for a week
_TERMINAL = {"completed", "failed", "cancelled"}
_UNFINISHED = {"pending", "running"}
INTERRUPTED_MESSAGE = (
    "This run stopped making progress (the server restarted or a worker was lost) and was marked failed. "
    "Start it again."
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _archive_dir() -> Path:
    return Path(os.environ.get("SEARCHOPS_REPORT_DIR", "data/reports"))


def _archive_path(job_id: str) -> Path | None:
    # Job ids are UUIDs; anything else must never become a filesystem path.
    if not _SAFE_ID.match(job_id):
        return None
    return _archive_dir() / f"{job_id}.json"


def _status_value(state: dict[str, Any]) -> str:
    status = state.get("status", "")
    return str(getattr(status, "value", status)).lower()


_JOB_KEY_PREFIX = "research:job:"
_EVENTS_CHANNEL_PREFIX = "searchops:job:"
_EVENTS_CHANNEL_SUFFIX = ":events"
_UNFINISHED_SET = "research:jobs:unfinished"
# A running job's lease outlives this many seconds without a refresh only if
# its process is gone. Refreshed every LEASE_TTL_SECONDS / 3.
LEASE_TTL_SECONDS = 45


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


def _channel(job_id: str) -> str:
    return f"{_EVENTS_CHANNEL_PREFIX}{job_id}{_EVENTS_CHANNEL_SUFFIX}"


def _lease_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}:lease"


class JobStateManager:
    """Owns the full lifecycle of research jobs.

    All state reads and writes MUST go through this class.
    Never call RedisCache directly from service or handler code.
    """

    def __init__(self, cache: RedisCache, event_bus: RedisEventBus) -> None:
        self._cache = cache
        self._event_bus = event_bus

    # ── Write operations ──────────────────────────────────────────────────────

    async def create_job(self, job_id: str, initial_state: dict[str, Any]) -> None:
        """Persist initial job state and publish creation event.

        Args:
            job_id: Unique job identifier (UUID string).
            initial_state: Dict conforming to the job state schema.
        """
        state = {**initial_state, "job_id": job_id, "updated_at": time.time()}
        await self._cache.set(_job_key(job_id), state, ttl_seconds=_JOB_TTL)
        await self._redis("sadd", _UNFINISHED_SET, job_id)
        await self._event_bus.publish(_channel(job_id), state)
        log.info("Job state created", job_id=job_id)

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Atomically merge patch into current state, persist, and publish.

        Args:
            job_id: The job to update.
            patch: Fields to merge into the current state. Existing fields not
                   in patch are preserved.

        Returns:
            The merged state dict after applying the patch.
        """
        current = await self._cache.get(_job_key(job_id)) or {}
        merged = {**current, **patch, "job_id": job_id, "updated_at": time.time()}
        finished = _status_value(merged) in _TERMINAL
        await self._cache.set(_job_key(job_id), merged, ttl_seconds=_FINISHED_JOB_TTL if finished else _JOB_TTL)
        await self._event_bus.publish(_channel(job_id), merged)
        if finished:
            await self._redis("srem", _UNFINISHED_SET, job_id)
            await self._archive(job_id, merged)
        return merged

    # ── Leases (which process is running a job) ──────────────────────────────

    async def _redis(self, command: str, *args: Any, **kwargs: Any) -> Any:
        """Run a raw Redis command; bookkeeping failures are logged, never raised."""
        try:
            return await getattr(self._cache.client, command)(*args, **kwargs)
        except Exception as exc:
            log.warning("Job bookkeeping command failed", command=command, error=str(exc))
            return None

    async def claim_lease(self, job_id: str, runner_id: str) -> bool:
        """Take the lease if nobody holds it. True when this runner now holds it."""
        return bool(await self._redis("set", _lease_key(job_id), runner_id, nx=True, ex=LEASE_TTL_SECONDS))

    async def renew_lease(self, job_id: str, runner_id: str) -> None:
        await self._redis("set", _lease_key(job_id), runner_id, ex=LEASE_TTL_SECONDS)

    async def release_lease(self, job_id: str, runner_id: str) -> None:
        holder = await self._redis("get", _lease_key(job_id))
        if holder is not None and (holder.decode() if isinstance(holder, bytes) else str(holder)) == runner_id:
            await self._redis("delete", _lease_key(job_id))

    async def unfinished_job_ids(self) -> list[str]:
        members = await self._redis("smembers", _UNFINISHED_SET) or set()
        return sorted(m.decode() if isinstance(m, bytes) else str(m) for m in members)

    async def forget_unfinished(self, job_id: str) -> None:
        await self._redis("srem", _UNFINISHED_SET, job_id)

    async def count_daily_start(self, owner_id: str) -> int | None:
        """Count one run started today by *owner_id*; returns today's total (None if unknown)."""
        key = f"research:quota:{owner_id}:{time.strftime('%Y%m%d', time.gmtime())}"
        total = await self._redis("incr", key)
        if total is None:
            return None
        if int(total) == 1:
            await self._redis("expire", key, 2 * 24 * 3600)
        return int(total)

    async def get_raw(self, job_id: str) -> dict[str, Any] | None:
        """Persisted state without the staleness check (for the watchdog)."""
        state = await self._cache.get(_job_key(job_id))
        return state if isinstance(state, dict) else None

    async def _archive(self, job_id: str, state: dict[str, Any]) -> None:
        """Write a finished job to disk. Failure is logged, never raised."""
        path = _archive_path(job_id)
        if path is None:
            return

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_bytes(orjson.dumps(json_safe(state)))
            tmp.replace(path)

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            log.warning("Job archive write failed", job_id=job_id, error=str(exc))

    async def _load_archived(self, job_id: str) -> dict[str, Any] | None:
        path = _archive_path(job_id)
        if path is None:
            return None
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except (FileNotFoundError, OSError):
            return None
        try:
            state = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None
        return state if isinstance(state, dict) else None

    async def list_archived(self, limit: int = 50) -> list[dict[str, Any]]:
        """Summaries of finished jobs on disk, newest first."""

        def _scan() -> list[Path]:
            d = _archive_dir()
            if not d.is_dir():
                return []
            return sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]

        out: list[dict[str, Any]] = []
        for path in await asyncio.to_thread(_scan):
            state = await self._load_archived(path.stem)
            if state:
                out.append(
                    {
                        "job_id": state.get("job_id", path.stem),
                        "query": state.get("query", ""),
                        "status": _status_value(state),
                        "completed_at": state.get("completed_at", ""),
                        "source_count": len(state.get("report_sources") or state.get("citations") or []),
                        "claim_count": len(state.get("evidence_claims") or []),
                    }
                )
        return out

    # ── Read operations ───────────────────────────────────────────────────────

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve current persisted job state.

        Returns:
            State dict if job exists, None otherwise. Falls back to the
            on-disk archive for finished jobs Redis no longer holds.
        """
        state = await self._cache.get(_job_key(job_id))
        if state is None:
            return await self._load_archived(job_id)
        return await self._fail_if_stale(job_id, state)

    async def _fail_if_stale(self, job_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """Report an unfinished job whose heartbeat stopped as failed.

        Every node completion refreshes ``updated_at``. A job still "running"
        long after its last update lost its process; saying so beats leaving
        the user watching a spinner forever.
        """
        from searchops.config.settings import get_settings

        stale_after = get_settings().api.job_stale_seconds
        updated = state.get("updated_at")
        if (
            stale_after <= 0
            or _status_value(state) not in _UNFINISHED
            or not isinstance(updated, (int, float))
            or time.time() - float(updated) < stale_after
        ):
            return state
        log.warning("Job heartbeat lost; marking failed", job_id=job_id, idle_seconds=int(time.time() - updated))
        return await self.update_job(job_id, {"status": "failed", "progress": 100, "error": INTERRUPTED_MESSAGE})

    async def get_or_replay(self, job_id: str) -> dict[str, Any] | None:
        """Return current state for late-subscriber replay.

        Called by WebSocket handlers immediately after connection.
        If the job is already completed, the client receives the final
        state snapshot without waiting for any pub/sub events.

        Returns:
            Current state dict (may be completed) or None if job not found.
        """
        return await self.get_job(job_id)

    async def delete_job(self, job_id: str) -> None:
        """Remove job from cache (explicit eviction on DELETE request)."""
        await self._cache.delete(_job_key(job_id))
        await self.forget_unfinished(job_id)
        path = _archive_path(job_id)
        if path is not None:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        log.info("Job state deleted", job_id=job_id)

    # ── Streaming ─────────────────────────────────────────────────────────────

    async def subscribe(self, job_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to real-time job state updates via Redis pub/sub.

        Yields state snapshots as they are published by `update_job()`.
        The generator exits when a state with status 'completed' or 'failed'
        is received, or when the caller's async context is cancelled.

        This is event-driven — no polling loops, no sleep().

        Usage (in WebSocket handler):
            async for state in job_state_manager.subscribe(job_id):
                await ws.send_text(orjson.dumps(state).decode())
                if state.get("status") in ("completed", "failed"):
                    break
        """
        redis_client = self._cache.client
        channel_name = _channel(job_id)

        async with redis_client.pubsub() as pubsub:
            await pubsub.subscribe(channel_name)
            log.info("Subscribed to job pub/sub channel", job_id=job_id, channel=channel_name)

            try:
                async for raw_message in pubsub.listen():
                    if raw_message["type"] != "message":
                        continue

                    try:
                        state = orjson.loads(raw_message["data"])
                    except (orjson.JSONDecodeError, TypeError) as exc:
                        log.warning(
                            "Failed to decode pub/sub message",
                            job_id=job_id,
                            error=str(exc),
                        )
                        continue

                    yield state

                    if state.get("status") in ("completed", "failed"):
                        log.info(
                            "Job terminal state received; closing subscription",
                            job_id=job_id,
                            status=state.get("status"),
                        )
                        break

            except asyncio.CancelledError:
                log.info("Job subscription cancelled", job_id=job_id)
            finally:
                await pubsub.unsubscribe(channel_name)
                log.info("Unsubscribed from job pub/sub channel", job_id=job_id)
