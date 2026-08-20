"""
Bright Data Circuit Breakers — Per-tier fault isolation.

Provides dedicated circuit breaker instances for each BD product:
  - BD SERP (search API)
  - BD Unlocker (residential proxy)
  - BD Browser (cloud CDP)
  - BD Dataset (entity API)

Design mirrors the existing CircuitBreaker in search/health.py but:
  - One instance per BD product (not shared across tiers)
  - asyncio.Lock for coroutine-safe state transitions
  - Emits Prometheus metrics on every state change
  - Configurable per-tier failure threshold and recovery timeout

States:
  CLOSED   — Normal operation. All requests pass through.
  OPEN     — Failure threshold exceeded. All requests rejected immediately.
  HALF_OPEN — Cooldown expired. One probe request allowed to test recovery.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum

import structlog

from searchops.scraping.bd_metrics import BD_CIRCUIT_TRIPS, BD_CIRCUIT_RECOVERIES

log = structlog.get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BrightDataCircuitBreaker:
    """
    Async-safe circuit breaker for a single Bright Data product tier.

    Example:
        breaker = bd_unlocker_breaker

        if not breaker.can_execute():
            raise CircuitOpenError("BD Unlocker circuit is open")

        try:
            result = await unlocker.scrape(request)
            breaker.record_success()
        except Exception:
            breaker.record_failure()
            raise
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 60.0,
        half_open_max_calls: int = 1,
    ) -> None:
        """
        Args:
            name: Tier identifier for metrics labels (e.g. 'bd_unlocker').
            failure_threshold: Consecutive failures before tripping OPEN.
            recovery_timeout_sec: Seconds in OPEN state before HALF_OPEN probe.
            half_open_max_calls: Max concurrent probes in HALF_OPEN state.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_state_change: float = time.monotonic()
        self._half_open_calls: int = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def can_execute(self) -> bool:
        """
        Check if the circuit allows a new request without acquiring the lock.

        This is a fast-path check safe for read access in the hot path.
        State transitions are guarded by the lock in record_success/failure.

        Returns:
            True if the request should proceed.
        """
        state = self._state

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # Check if cooldown has expired — transition to HALF_OPEN
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout_sec:
                # Optimistic read — actual transition happens in record_*
                return True  # Allow the probe request
            return False

        # HALF_OPEN: allow up to half_open_max_calls concurrent probes
        return self._half_open_calls < self.half_open_max_calls

    async def record_success(self) -> None:
        """
        Record a successful execution.

        If in HALF_OPEN, recovers to CLOSED and resets failure count.
        """
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                await self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._half_open_calls = max(0, self._half_open_calls - 1)

    async def record_failure(self) -> None:
        """
        Record a failed execution.

        In CLOSED: increments failure count; trips to OPEN if threshold exceeded.
        In HALF_OPEN: immediately re-trips to OPEN.
        In OPEN: refreshes timestamp if a probe slipped through.
        """
        async with self._lock:
            elapsed = time.monotonic() - self._last_state_change

            if self._state == CircuitState.OPEN:
                # A probe may have slipped through between can_execute() and here
                if elapsed >= self.recovery_timeout_sec:
                    await self._transition_to(CircuitState.HALF_OPEN)
                    self._failure_count += 1
                    await self._transition_to(CircuitState.OPEN)
                return

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls = max(0, self._half_open_calls - 1)
                await self._transition_to(CircuitState.OPEN)
                return

            # CLOSED state
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                await self._transition_to(CircuitState.OPEN)

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Perform state transition and emit metrics."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.monotonic()

        log.warning(
            "bd_circuit_breaker.transition",
            tier=self.name,
            old_state=old_state,
            new_state=new_state,
            failure_count=self._failure_count,
        )

        if new_state == CircuitState.OPEN:
            BD_CIRCUIT_TRIPS.labels(tier=self.name).inc()
        elif new_state == CircuitState.CLOSED and old_state != CircuitState.CLOSED:
            BD_CIRCUIT_RECOVERIES.labels(tier=self.name).inc()
            self._failure_count = 0

        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0

    def get_state_info(self) -> dict[str, object]:
        """Return circuit state for health endpoint exposure."""
        return {
            "tier": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "recovery_timeout_sec": self.recovery_timeout_sec,
            "seconds_in_current_state": time.monotonic() - self._last_state_change,
        }


# ── Module-level singleton breakers — one per BD product ─────────────────────

bd_serp_breaker = BrightDataCircuitBreaker(
    name="bd_serp",
    failure_threshold=3,
    recovery_timeout_sec=60.0,
)

bd_unlocker_breaker = BrightDataCircuitBreaker(
    name="bd_unlocker",
    failure_threshold=3,
    recovery_timeout_sec=60.0,
)

bd_browser_breaker = BrightDataCircuitBreaker(
    name="bd_browser",
    failure_threshold=2,     # Lower threshold — more expensive per call
    recovery_timeout_sec=120.0,  # Longer cooldown for cloud browser
)

bd_dataset_breaker = BrightDataCircuitBreaker(
    name="bd_dataset",
    failure_threshold=3,
    recovery_timeout_sec=60.0,
)

_BREAKERS: dict[str, BrightDataCircuitBreaker] = {
    "bd_serp": bd_serp_breaker,
    "bd_unlocker": bd_unlocker_breaker,
    "bd_browser": bd_browser_breaker,
    "bd_dataset": bd_dataset_breaker,
}


def get_bd_breaker(tier_name: str) -> BrightDataCircuitBreaker:
    """
    Return the circuit breaker for a given BD tier name.

    Args:
        tier_name: One of 'bd_serp', 'bd_unlocker', 'bd_browser', 'bd_dataset'.

    Returns:
        The corresponding BrightDataCircuitBreaker singleton.
        Falls back to bd_unlocker_breaker for unknown tier names.
    """
    return _BREAKERS.get(tier_name, bd_unlocker_breaker)


def get_all_breaker_states() -> list[dict[str, object]]:
    """Return health state for all BD circuit breakers (for /health endpoint)."""
    return [b.get_state_info() for b in _BREAKERS.values()]
