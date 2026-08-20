"""
Bright Data Distributed Spend Guard.

Enforces per-job, per-agent, per-hour, and per-day spending limits on
Bright Data premium tier usage using Redis atomic operations.

Key design decisions:

1. Redis Lua script for check-and-increment atomicity:
   - A single Lua call reads current spend, checks limit, and increments atomically
   - No TOCTOU race: 100 workers cannot bypass a $10 budget simultaneously

2. Fail-closed on Redis unavailability:
   - If Redis is unreachable, ALL BD calls are rejected (configurable)
   - Prevents unlimited credit burn during infrastructure failures

3. TTL-scoped keys:
   - Per-job key: no TTL (job-scoped, cleaned up by job ID)
   - Per-hour key: 3600s TTL (auto-expires after the hour window)
   - Per-day key: 86400s TTL (auto-expires after the day window)

4. Rollback on tier failure:
   - If a BD call succeeds the spend check but then fails to execute,
     the reserved cost is decremented back via INCRBYFLOAT with negative value
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

from searchops.scraping.bd_metrics import BD_BUDGET_REJECTIONS, BD_SPEND_USD

log = structlog.get_logger(__name__)

# Lua script: atomic check-and-increment
# Returns 1 if approved (and incremented), 0 if rejected (over budget)
_CHECK_AND_INCREMENT_LUA = """
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl   = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', key) or '0')
if limit > 0 and (current + cost) > limit then
    return 0
end

redis.call('INCRBYFLOAT', key, cost)
if ttl > 0 then
    redis.call('EXPIRE', key, ttl)
end
return 1
"""


class SpendDecision(str, Enum):
    APPROVED = "approved"
    SOFT_WARNING = "soft_warning"    # Within budget but above 80% threshold
    HARD_REJECTED = "hard_rejected"  # Over limit — call must not proceed


@dataclass
class SpendSummary:
    """Spend breakdown for a given job."""
    job_id: str
    job_spent_usd: float
    hour_spent_usd: float
    day_spent_usd: float
    job_limit_usd: float
    hour_limit_usd: float
    day_limit_usd: float

    @property
    def job_remaining_usd(self) -> float:
        return max(0.0, self.job_limit_usd - self.job_spent_usd)

    @property
    def hour_remaining_usd(self) -> float:
        return max(0.0, self.hour_limit_usd - self.hour_spent_usd)


class BrightDataSpendGuard:
    """
    Redis-backed distributed spend guard for Bright Data premium tiers.

    Usage in pipeline:
        decision = await spend_guard.check_and_reserve(
            cost_usd=tier.cost_usd,
            job_id=job_id,
            agent_id=agent_id,
            tier=tier.name,
        )
        if decision == SpendDecision.HARD_REJECTED:
            return _budget_exceeded_result(request)

        try:
            result = await tier.scraper.scrape(request)
        except Exception:
            await spend_guard.rollback(cost_usd, job_id, agent_id, tier.name)
            raise
    """

    def __init__(
        self,
        redis_client: Any,
        max_per_job_usd: float = 0.50,
        max_per_agent_usd: float = 2.00,
        max_per_hour_usd: float = 5.00,
        max_per_day_usd: float = 20.00,
        fail_closed: bool = True,
    ) -> None:
        """
        Args:
            redis_client: Async Redis client instance (from infrastructure.cache.redis).
            max_per_job_usd: Hard limit per scraping job. 0 = unlimited.
            max_per_agent_usd: Hard limit per agent per day. 0 = unlimited.
            max_per_hour_usd: Platform-wide hourly limit. 0 = unlimited.
            max_per_day_usd: Platform-wide daily limit. 0 = unlimited.
            fail_closed: If True, reject BD calls when Redis is unreachable.
                         If False, allow calls through on Redis failure (fail-open).
        """
        self._redis = redis_client
        self._max_per_job = max_per_job_usd
        self._max_per_agent = max_per_agent_usd
        self._max_per_hour = max_per_hour_usd
        self._max_per_day = max_per_day_usd
        self._fail_closed = fail_closed

    # ── Key builders ─────────────────────────────────────────────────────────

    def _job_key(self, job_id: str) -> str:
        return f"bd:spend:job:{job_id}"

    def _agent_key(self, agent_id: str) -> str:
        day = time.strftime("%Y-%m-%d")
        return f"bd:spend:agent:{agent_id}:{day}"

    def _hour_key(self) -> str:
        hour = time.strftime("%Y-%m-%dT%H")
        return f"bd:spend:hour:{hour}"

    def _day_key(self) -> str:
        day = time.strftime("%Y-%m-%d")
        return f"bd:spend:day:{day}"

    # ── Core API ─────────────────────────────────────────────────────────────

    async def check_and_reserve(
        self,
        cost_usd: float,
        job_id: str,
        tier: str,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> SpendDecision:
        """
        Atomically check budget and reserve cost if approved.

        All scope checks use Lua for atomic check-and-increment.
        A single failure in any scope causes overall HARD_REJECTED.

        Args:
            cost_usd: Estimated cost of the BD operation to reserve.
            job_id: Current job identifier.
            tier: BD product tier name (for metrics labels).
            agent_id: Optional agent identifier for per-agent limits.
            tenant_id: Optional tenant identifier (reserved for future use).

        Returns:
            SpendDecision indicating whether the call should proceed.
        """
        if cost_usd <= 0:
            return SpendDecision.APPROVED

        try:
            # Check and reserve across all budget scopes
            approved = await self._check_scope(
                key=self._job_key(job_id),
                cost=cost_usd,
                limit=self._max_per_job,
                ttl=0,  # Job keys don't expire — cleaned up by job lifecycle
            )
            if not approved:
                log.warning("bd_spend_guard: job budget exceeded", job_id=job_id, cost=cost_usd)
                BD_BUDGET_REJECTIONS.labels(scope="job", tier=tier).inc()
                return SpendDecision.HARD_REJECTED

            approved = await self._check_scope(
                key=self._hour_key(),
                cost=cost_usd,
                limit=self._max_per_hour,
                ttl=3600,
            )
            if not approved:
                # Rollback job increment
                await self._rollback_scope(self._job_key(job_id), cost_usd)
                log.warning("bd_spend_guard: hourly budget exceeded", cost=cost_usd)
                BD_BUDGET_REJECTIONS.labels(scope="hour", tier=tier).inc()
                return SpendDecision.HARD_REJECTED

            approved = await self._check_scope(
                key=self._day_key(),
                cost=cost_usd,
                limit=self._max_per_day,
                ttl=86400,
            )
            if not approved:
                # Rollback job + hour increments
                await self._rollback_scope(self._job_key(job_id), cost_usd)
                await self._rollback_scope(self._hour_key(), cost_usd)
                log.warning("bd_spend_guard: daily budget exceeded", cost=cost_usd)
                BD_BUDGET_REJECTIONS.labels(scope="day", tier=tier).inc()
                return SpendDecision.HARD_REJECTED

            if agent_id:
                approved = await self._check_scope(
                    key=self._agent_key(agent_id),
                    cost=cost_usd,
                    limit=self._max_per_agent,
                    ttl=86400,
                )
                if not approved:
                    # Rollback all previous increments
                    await self._rollback_scope(self._job_key(job_id), cost_usd)
                    await self._rollback_scope(self._hour_key(), cost_usd)
                    await self._rollback_scope(self._day_key(), cost_usd)
                    log.warning("bd_spend_guard: agent budget exceeded", agent_id=agent_id)
                    BD_BUDGET_REJECTIONS.labels(scope="agent", tier=tier).inc()
                    return SpendDecision.HARD_REJECTED

            # Record metric spend
            BD_SPEND_USD.labels(tier=tier).inc(cost_usd)
            return SpendDecision.APPROVED

        except Exception as exc:
            log.error("bd_spend_guard: Redis error", error=str(exc))
            if self._fail_closed:
                log.warning("bd_spend_guard: fail-closed — rejecting BD call due to Redis unavailability")
                return SpendDecision.HARD_REJECTED
            log.warning("bd_spend_guard: fail-open — allowing BD call despite Redis unavailability")
            return SpendDecision.APPROVED

    async def rollback(
        self,
        cost_usd: float,
        job_id: str,
        tier: str,
        agent_id: str | None = None,
    ) -> None:
        """
        Rollback a previously reserved spend amount.

        Called when a BD tier fails after the spend was reserved.
        Uses INCRBYFLOAT with a negative value to decrement atomically.

        Args:
            cost_usd: The amount to roll back.
            job_id: Job identifier used in the original reservation.
            tier: BD product tier name (for logging).
            agent_id: Optional agent identifier used in the original reservation.
        """
        if cost_usd <= 0:
            return
        try:
            await self._rollback_scope(self._job_key(job_id), cost_usd)
            await self._rollback_scope(self._hour_key(), cost_usd)
            await self._rollback_scope(self._day_key(), cost_usd)
            if agent_id:
                await self._rollback_scope(self._agent_key(agent_id), cost_usd)
            log.info("bd_spend_guard: rolled back spend", tier=tier, cost=cost_usd)
        except Exception as exc:
            log.error("bd_spend_guard: rollback failed", error=str(exc))

    async def get_summary(self, job_id: str) -> SpendSummary:
        """
        Return current spend breakdown for a job.

        Args:
            job_id: The job to retrieve spend for.

        Returns:
            SpendSummary with per-scope spend and limits.
        """
        try:
            job_val = await self._redis.get(self._job_key(job_id))
            hour_val = await self._redis.get(self._hour_key())
            day_val = await self._redis.get(self._day_key())
        except Exception:
            job_val = hour_val = day_val = None

        return SpendSummary(
            job_id=job_id,
            job_spent_usd=float(job_val or 0),
            hour_spent_usd=float(hour_val or 0),
            day_spent_usd=float(day_val or 0),
            job_limit_usd=self._max_per_job,
            hour_limit_usd=self._max_per_hour,
            day_limit_usd=self._max_per_day,
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _check_scope(
        self,
        key: str,
        cost: float,
        limit: float,
        ttl: int,
    ) -> bool:
        """
        Atomically check if adding cost would exceed limit, and increment if not.

        Uses a Lua script for atomic read-check-write (no TOCTOU race).

        Args:
            key: Redis key for this budget scope.
            cost: Cost to add if within limit.
            limit: Maximum allowed value. 0 means unlimited.
            ttl: Key TTL in seconds. 0 means no expiry.

        Returns:
            True if the cost was reserved (under limit), False if rejected.
        """
        result = await self._redis.eval(
            _CHECK_AND_INCREMENT_LUA,
            1,           # Number of KEYS
            key,         # KEYS[1]
            str(cost),   # ARGV[1]
            str(limit),  # ARGV[2]
            str(ttl),    # ARGV[3]
        )
        return bool(result)

    async def _rollback_scope(self, key: str, cost: float) -> None:
        """Decrement a budget key by cost (rollback a reservation)."""
        try:
            # INCRBYFLOAT with negative value is atomic and safe
            await self._redis.incrbyfloat(key, -cost)
        except Exception:
            pass  # Best-effort rollback


def build_spend_guard(
    redis_client: Any,
    max_per_job_usd: float = 0.50,
    max_per_hour_usd: float = 5.00,
    max_per_day_usd: float = 20.00,
    fail_closed: bool = True,
) -> BrightDataSpendGuard:
    """
    Factory for BrightDataSpendGuard with configuration from settings.

    Args:
        redis_client: Async Redis client.
        max_per_job_usd: Per-job hard limit.
        max_per_hour_usd: Platform hourly limit.
        max_per_day_usd: Platform daily limit.
        fail_closed: Whether to reject BD calls if Redis is unavailable.

    Returns:
        Configured BrightDataSpendGuard instance.
    """
    return BrightDataSpendGuard(
        redis_client=redis_client,
        max_per_job_usd=max_per_job_usd,
        max_per_hour_usd=max_per_hour_usd,
        max_per_day_usd=max_per_day_usd,
        fail_closed=fail_closed,
    )
