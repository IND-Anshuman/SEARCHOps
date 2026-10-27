"""
Unit tests for BrightDataCircuitBreaker: the per-tier async fault isolator.

Covers the CLOSED → OPEN → HALF_OPEN → CLOSED state machine, the
half-open probe budget, cooldown timing, metric emission, state-info
reporting, and the per-tier singleton registry.
"""

from __future__ import annotations

import pytest

from searchops.scraping import bd_circuit_breaker as cbm
from searchops.scraping.bd_circuit_breaker import (
    BrightDataCircuitBreaker,
    CircuitState,
    get_all_breaker_states,
    get_bd_breaker,
)


@pytest.fixture
def breaker():
    return BrightDataCircuitBreaker(name="test_tier", failure_threshold=3, recovery_timeout_sec=1.0)


class FakeClock:
    """Replaces time.monotonic with an explicitly advanced value."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """Install a controllable monotonic clock into the breaker module."""
    c = FakeClock()
    monkeypatch.setattr(cbm.time, "monotonic", c.monotonic)
    return c


# ── Initial state ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_starts_closed_and_executable(breaker):
    assert breaker.state is CircuitState.CLOSED
    assert breaker.can_execute() is True
    assert breaker.failure_threshold == 3
    assert breaker.half_open_max_calls == 1


# ── Failure accumulation and tripping ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stays_closed_below_threshold(breaker):
    await breaker.record_failure()
    await breaker.record_failure()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.can_execute() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trips_open_at_threshold(breaker):
    for _ in range(3):
        await breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    assert breaker.can_execute() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_resets_failure_count(breaker):
    await breaker.record_failure()
    await breaker.record_failure()
    await breaker.record_success()
    await breaker.record_failure()
    await breaker.record_failure()

    assert breaker.state is CircuitState.CLOSED, "Success clears the failure streak"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_rejects_without_waiting_for_cooldown(breaker):
    for _ in range(3):
        await breaker.record_failure()

    assert breaker.can_execute() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_expiry_allows_probe(breaker, clock):
    for _ in range(3):
        await breaker.record_failure()

    clock.advance(2.0)  # past the 1.0s recovery timeout

    assert breaker.can_execute() is True, "A probe is allowed after the cooldown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_half_open_state_permits_only_one_probe(breaker, clock):
    for _ in range(3):
        await breaker.record_failure()

    clock.advance(2.0)
    assert breaker.can_execute() is True

    # Simulate a probe in flight.
    await breaker._transition_to(CircuitState.HALF_OPEN)
    breaker._half_open_calls = 1

    assert breaker.can_execute() is False, "Only half_open_max_calls probes allowed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_half_open_probe_limit_is_configurable():
    breaker = BrightDataCircuitBreaker(name="t", failure_threshold=1, recovery_timeout_sec=0.0, half_open_max_calls=3)
    await breaker._transition_to(CircuitState.HALF_OPEN)
    breaker._half_open_calls = 2

    assert breaker.can_execute() is True, "A third probe is still allowed"
    breaker._half_open_calls = 3
    assert breaker.can_execute() is False


# ── Recovery ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_in_half_open_closes_circuit(breaker):
    for _ in range(3):
        await breaker.record_failure()
    await breaker._transition_to(CircuitState.HALF_OPEN)

    await breaker.record_success()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.can_execute() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failure_in_half_open_reopens_circuit(breaker):
    for _ in range(3):
        await breaker.record_failure()
    await breaker._transition_to(CircuitState.HALF_OPEN)

    await breaker.record_failure()

    assert breaker.state is CircuitState.OPEN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failure_while_open_after_cooldown_reopens(breaker, clock):
    for _ in range(3):
        await breaker.record_failure()

    # A probe slipped through after the cooldown and then failed.
    clock.advance(2.0)
    await breaker.record_failure()

    assert breaker.state is CircuitState.OPEN


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failure_while_open_before_cooldown_is_ignored(breaker):
    for _ in range(3):
        await breaker.record_failure()
    count_before = breaker._failure_count

    await breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    assert breaker._failure_count == count_before, "No extra failure recorded during cooldown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_half_open_calls_never_go_negative(breaker):
    await breaker._transition_to(CircuitState.HALF_OPEN)
    await breaker.record_success()

    assert breaker._half_open_calls == 0


# ── Metrics and state reporting ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_info_shape(breaker):
    info = breaker.get_state_info()

    assert info["tier"] == "test_tier"
    assert info["state"] == "closed"
    assert info["failure_count"] == 0
    assert "recovery_timeout_sec" in info
    assert "seconds_in_current_state" in info


@pytest.mark.unit
def test_state_info_omits_failure_threshold():
    """GAP PINNED: the health payload does not expose the failure threshold.

    An operator reading /health sees the current count but not the limit that
    trips the breaker, so they cannot tell how close a tier is to opening.
    """
    assert "failure_threshold" not in BrightDataCircuitBreaker(name="t").get_state_info()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_info_reflects_open_state(breaker):
    for _ in range(3):
        await breaker.record_failure()

    assert breaker.get_state_info()["state"] == "open"
    assert breaker.get_state_info()["failure_count"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_emits_trip_metric(breaker, monkeypatch):
    calls: list[str] = []

    class FakeMetric:
        def labels(self, tier: str):
            calls.append(f"trip:{tier}")
            return self

        def inc(self, *a):
            calls.append("inc")

    monkeypatch.setattr(cbm, "BD_CIRCUIT_TRIPS", FakeMetric())

    for _ in range(3):
        await breaker.record_failure()

    assert "trip:test_tier" in calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_emits_recovery_metric(breaker, monkeypatch):
    calls: list[str] = []

    class FakeMetric:
        def labels(self, tier: str):
            calls.append(f"rec:{tier}")
            return self

        def inc(self, *a):
            calls.append("inc")

    monkeypatch.setattr(cbm, "BD_CIRCUIT_RECOVERIES", FakeMetric())

    for _ in range(3):
        await breaker.record_failure()
    await breaker._transition_to(CircuitState.HALF_OPEN)
    await breaker.record_success()

    assert "rec:test_tier" in calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_recovery_metric_on_plain_closed_success(breaker, monkeypatch):
    calls: list[str] = []

    class FakeMetric:
        def labels(self, tier: str):
            calls.append("x")
            return self

        def inc(self, *a):
            calls.append("inc")

    monkeypatch.setattr(cbm, "BD_CIRCUIT_RECOVERIES", FakeMetric())

    await breaker.record_success()  # already CLOSED

    assert calls == [], "No recovery metric when the circuit was never open"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transition_to_half_open_resets_probe_counter(breaker):
    breaker._half_open_calls = 5
    await breaker._transition_to(CircuitState.HALF_OPEN)

    assert breaker._half_open_calls == 0


# ── Concurrency safety ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_failures_trip_exactly_once():
    """Many concurrent failures must all be counted under the lock."""
    breaker = BrightDataCircuitBreaker(name="t", failure_threshold=10, recovery_timeout_sec=1.0)

    import asyncio

    await asyncio.gather(*[breaker.record_failure() for _ in range(10)])

    assert breaker.state is CircuitState.OPEN
    assert breaker._failure_count == 10, "Every failure is counted exactly once"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lock_is_released_after_failure(breaker):
    await breaker.record_failure()
    assert breaker._lock.locked() is False


# ── Per-tier registry ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_bd_breaker_returns_known_tiers():
    for tier in ("bd_serp", "bd_unlocker", "bd_browser", "bd_dataset"):
        assert get_bd_breaker(tier) is not None


@pytest.mark.unit
def test_get_bd_breaker_falls_back_to_unlocker():
    assert get_bd_breaker("not_a_real_tier") is get_bd_breaker("bd_unlocker")


@pytest.mark.unit
def test_each_tier_has_its_own_breaker():
    assert get_bd_breaker("bd_serp") is not get_bd_breaker("bd_unlocker")


@pytest.mark.unit
def test_get_all_breaker_states_covers_every_tier():
    states = get_all_breaker_states()

    assert len(states) == 4
    tiers = {s["tier"] for s in states}
    assert tiers == {"bd_serp", "bd_unlocker", "bd_browser", "bd_dataset"}


@pytest.mark.unit
def test_state_info_is_a_copy():
    """Mutating the returned dict must not affect the breaker."""
    breaker = BrightDataCircuitBreaker(name="t")
    info = breaker.get_state_info()
    info["state"] = "tampered"

    assert breaker.state is CircuitState.CLOSED
