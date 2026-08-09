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
  - research:job:{job_id}          → orjson-serialized job state dict (TTL: 1h)
  - searchops:job:{job_id}:events  → Redis pub/sub channel for job updates

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
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import orjson
import structlog

from searchops.infrastructure.cache.redis import RedisCache
from searchops.infrastructure.events.bus import RedisEventBus

log = structlog.get_logger(__name__)

_JOB_TTL = 3600  # 1 hour
_JOB_KEY_PREFIX = "research:job:"
_EVENTS_CHANNEL_PREFIX = "searchops:job:"
_EVENTS_CHANNEL_SUFFIX = ":events"


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


def _channel(job_id: str) -> str:
    return f"{_EVENTS_CHANNEL_PREFIX}{job_id}{_EVENTS_CHANNEL_SUFFIX}"


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
        state = {**initial_state, "job_id": job_id}
        await self._cache.set(_job_key(job_id), state, ttl_seconds=_JOB_TTL)
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
        merged = {**current, **patch, "job_id": job_id}
        await self._cache.set(_job_key(job_id), merged, ttl_seconds=_JOB_TTL)
        await self._event_bus.publish(_channel(job_id), merged)
        return merged

    # ── Read operations ───────────────────────────────────────────────────────

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve current persisted job state.

        Returns:
            State dict if job exists, None otherwise.
        """
        return await self._cache.get(_job_key(job_id))

    async def get_or_replay(self, job_id: str) -> dict[str, Any] | None:
        """Return current state for late-subscriber replay.

        Called by WebSocket handlers immediately after connection.
        If the job is already completed, the client receives the final
        state snapshot without waiting for any pub/sub events.

        Returns:
            Current state dict (may be completed) or None if job not found.
        """
        return await self._cache.get(_job_key(job_id))

    async def delete_job(self, job_id: str) -> None:
        """Remove job from cache (explicit eviction on DELETE request)."""
        await self._cache.delete(_job_key(job_id))
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
