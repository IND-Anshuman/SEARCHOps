"""
Unit tests for BDCDPPool: the Bright Data CDP session pool.

Covers session health, the acquire/release semaphore protocol, recycle
scheduling, pool-size capping, health_check, shutdown, and the module-level
singleton accessors. `_create_session` is stubbed so no CDP connection and no
credentials are ever used.
"""

from __future__ import annotations

import asyncio

import pytest

from searchops.scraping import bd_browser_pool as bdp_mod
from searchops.scraping.bd_browser_pool import (
    BDCDPPool,
    BDSession,
    _close_browser,
    get_bd_cdp_pool,
    init_bd_cdp_pool,
    shutdown_bd_cdp_pool,
)

# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeCDPBrowser:
    def __init__(self, *, connected: bool = True, closed: bool = False) -> None:
        self._connected = connected
        self.closed = closed

    def is_connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        self.closed = True
        self._connected = False


class NoCloseBrowser:
    """A browser object with no close() attribute at all."""

    def is_connected(self) -> bool:
        return True


class SyncCloseBrowser:
    """close() returns a non-awaitable."""

    def __init__(self) -> None:
        self.closed = False

    def is_connected(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def _session(browser=None, *, request_count: int = 0) -> BDSession:
    s = BDSession(browser=browser or FakeCDPBrowser())
    s.request_count = request_count
    return s


def _pool(**overrides) -> BDCDPPool:
    """A pool pre-marked as initialized so acquire() is usable."""
    pool = BDCDPPool(**overrides)
    pool._initialized = True
    return pool


# ── _close_browser ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_browser_awaits_async_close():
    browser = FakeCDPBrowser()
    await _close_browser(browser)
    assert browser.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_browser_ignores_missing_close():
    await _close_browser(NoCloseBrowser())  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_browser_handles_synchronous_close():
    browser = SyncCloseBrowser()
    await _close_browser(browser)
    assert browser.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_browser_tolerates_none():
    await _close_browser(None)  # must not raise


# ── BDSession ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_session_healthy_when_browser_connected():
    assert _session(FakeCDPBrowser(connected=True)).is_healthy() is True


@pytest.mark.unit
def test_session_unhealthy_when_browser_disconnected():
    assert _session(FakeCDPBrowser(connected=False)).is_healthy() is False


@pytest.mark.unit
def test_session_healthy_when_is_connected_missing():
    """A browser without is_connected() defaults to healthy."""

    class Bare:
        pass

    assert _session(Bare()).is_healthy() is True


@pytest.mark.unit
def test_session_unhealthy_when_is_connected_raises():
    class Exploding:
        def is_connected(self):
            raise RuntimeError("dead socket")

    assert _session(Exploding()).is_healthy() is False


@pytest.mark.unit
def test_session_age_increases():
    s = _session()
    assert s.age_seconds >= 0


# ── Construction defaults ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_default_pool_parameters():
    pool = BDCDPPool()

    assert pool._max_connections == 5
    assert pool._recycle_after == 50
    assert pool._acquire_timeout == 30.0
    assert pool._initialized is False


@pytest.mark.unit
def test_pool_starts_empty():
    pool = BDCDPPool()

    assert pool._sessions == []
    assert pool._recycle_tasks == set()


# ── acquire() ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_requires_initialization():
    pool = BDCDPPool()  # not initialized

    with pytest.raises(RuntimeError, match="initialize"):
        async with pool.acquire():
            pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_creates_and_returns_session(monkeypatch):
    pool = _pool()
    created = [_session(FakeCDPBrowser())]
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(created[0]))

    async with pool.acquire() as session:
        assert session is created[0]

    assert pool._sessions == created, "Session is retained in the pool"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_reuses_existing_healthy_session(monkeypatch):
    pool = _pool()
    existing = _session(FakeCDPBrowser(connected=True))
    pool._sessions = [existing]

    async def never_called(self):
        raise AssertionError("A healthy session must be reused, not recreated")

    monkeypatch.setattr(BDCDPPool, "_create_session", never_called)

    async with pool.acquire() as session:
        assert session is existing


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_replaces_unhealthy_session(monkeypatch):
    pool = _pool()
    dead = _session(FakeCDPBrowser(connected=False))
    pool._sessions = [dead]
    fresh = _session(FakeCDPBrowser())
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(fresh))

    async with pool.acquire() as session:
        assert session is fresh


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_releases_semaphore_on_exit():
    pool = _pool(max_connections=1)
    session = _session()
    pool._sessions = [session]

    async with pool.acquire():
        assert pool._semaphore.locked() is True

    assert pool._semaphore.locked() is False, "Semaphore released on exit"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_releases_semaphore_on_exception():
    pool = _pool(max_connections=1)
    pool._sessions = [_session()]

    with pytest.raises(ValueError):
        async with pool.acquire():
            raise ValueError("caller blew up")

    assert pool._semaphore.locked() is False, "Slot must not leak on error"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_increments_request_count():
    pool = _pool()
    session = _session()
    pool._sessions = [session]

    async with pool.acquire():
        pass

    assert session.request_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_times_out_when_pool_exhausted():
    pool = _pool(max_connections=1, acquire_timeout_sec=0.05)
    pool._sessions = [_session()]
    # Drain the only slot.
    await pool._semaphore.acquire()

    with pytest.raises(TimeoutError):
        async with pool.acquire():
            pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_schedules_recycle_at_threshold(monkeypatch):
    pool = _pool(recycle_after=1)
    session = _session()
    pool._sessions = [session]

    recycled: list = []

    async def fake_recycle(self, s):
        recycled.append(s)

    monkeypatch.setattr(BDCDPPool, "_recycle_session", fake_recycle)

    async with pool.acquire():
        pass

    # The recycle task is created but not awaited; let it run.
    await asyncio.gather(*pool._recycle_tasks) if pool._recycle_tasks else None
    assert recycled == [session], "Recycle scheduled once the threshold is reached"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_does_not_recycle_below_threshold():
    pool = _pool(recycle_after=10)
    pool._sessions = [_session()]

    async with pool.acquire():
        pass

    assert pool._recycle_tasks == set(), "No recycle task below the threshold"


# ── Pool size capping ─────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pool_caps_sessions_at_max_connections(monkeypatch):
    pool = _pool(max_connections=1)
    existing = _session()
    pool._sessions = [existing]

    extra = _session()
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(extra))

    async with pool.acquire():
        pass

    assert len(pool._sessions) == 1, "An extra session is not admitted past the cap"
    assert extra not in pool._sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pool_admits_session_under_cap(monkeypatch):
    pool = _pool(max_connections=2)
    pool._sessions = []
    new = _session()
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(new))

    async with pool.acquire():
        pass

    assert new in pool._sessions


# ── health_check ──────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_false_when_uninitialized():
    assert await BDCDPPool().health_check() is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_true_with_healthy_session():
    pool = _pool()
    pool._sessions = [_session(FakeCDPBrowser(connected=True))]

    assert await pool.health_check() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_probes_when_all_sessions_dead(monkeypatch):
    pool = _pool()
    pool._sessions = [_session(FakeCDPBrowser(connected=False))]
    probe = _session(FakeCDPBrowser())
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(probe))

    assert await pool.health_check() is True
    assert probe.browser.closed is True, "The probe session is closed after probing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_false_when_probe_fails(monkeypatch):
    pool = _pool()
    pool._sessions = []

    async def boom(self):
        raise RuntimeError("cannot reach Bright Data")

    monkeypatch.setattr(BDCDPPool, "_create_session", boom)

    assert await pool.health_check() is False


# ── _recycle_session ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recycle_session_replaces_it(monkeypatch):
    pool = _pool()
    old = _session(FakeCDPBrowser())
    pool._sessions = [old]
    fresh = _session(FakeCDPBrowser())
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(fresh))

    await pool._recycle_session(old)

    assert old not in pool._sessions
    assert fresh in pool._sessions
    assert old.browser.closed is True, "The recycled browser is closed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recycle_session_unknown_session_is_safe(monkeypatch):
    pool = _pool()
    pool._sessions = []
    fresh = _session()
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(fresh))

    await pool._recycle_session(_session())  # not in the pool

    assert fresh in pool._sessions


# ── initialize / shutdown ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_marks_pool_and_prewarms(monkeypatch):
    pool = BDCDPPool()
    prewarm = _session(FakeCDPBrowser())
    monkeypatch.setattr(BDCDPPool, "_create_session", lambda self: _async(prewarm))

    await pool.initialize(customer_id="c", zone="z", password="p")

    assert pool._initialized is True
    assert pool._customer_id == "c"
    assert pool._zone == "z"
    assert prewarm in pool._sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_is_idempotent(monkeypatch):
    pool = BDCDPPool()
    pool._initialized = True

    async def boom(self):
        raise AssertionError("initialize() must return early when already done")

    monkeypatch.setattr(BDCDPPool, "_create_session", boom)

    await pool.initialize(customer_id="c", zone="z", password="p")
    assert pool._initialized is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_propagates_prewarm_failure(monkeypatch):
    """A failed prewarm propagates and leaves the pool uninitialized.

    Pinned deliberately: initialize() re-raises, so a bad zone surfaces at
    startup instead of silently yielding a pool that cannot serve requests.
    """
    pool = BDCDPPool()

    async def boom(self):
        raise RuntimeError("no zone available")

    monkeypatch.setattr(BDCDPPool, "_create_session", boom)

    with pytest.raises(RuntimeError, match="no zone available"):
        await pool.initialize(customer_id="c", zone="z", password="p")

    assert pool._initialized is False, "Pool is not usable after a failed prewarm"
    assert pool._sessions == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_closes_all_sessions():
    pool = _pool()
    browsers = [FakeCDPBrowser(), FakeCDPBrowser()]
    pool._sessions = [_session(b) for b in browsers]

    await pool.shutdown()

    assert all(b.closed for b in browsers)
    assert pool._sessions == []
    assert pool._initialized is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_with_no_sessions_is_safe():
    pool = _pool()
    await pool.shutdown()  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_does_not_clear_credentials():
    """GAP PINNED: shutdown() leaves the BD credentials on the instance.

    The module docstring says credentials are "stored transiently, not as
    loggable attributes", but shutdown() clears sessions and the initialized
    flag only — the customer id, zone and password survive on the object for
    as long as the pool reference is alive.
    """
    pool = _pool()
    pool._customer_id = "secret-customer"
    pool._zone = "secret-zone"
    pool._password = "secret-password"
    pool._sessions = []

    await pool.shutdown()

    assert pool._customer_id == "secret-customer"
    assert pool._zone == "secret-zone"
    assert pool._password == "secret-password"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_is_idempotent():
    pool = _pool()
    pool._sessions = []
    await pool.shutdown()
    await pool.shutdown()  # must not raise


# ── Module singletons ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_bd_cdp_pool_none_before_init():
    bdp_mod._bd_cdp_pool = None
    try:
        assert get_bd_cdp_pool() is None
    finally:
        bdp_mod._bd_cdp_pool = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_init_bd_cdp_pool_sets_singleton(monkeypatch):
    bdp_mod._bd_cdp_pool = None
    monkeypatch.setattr(BDCDPPool, "initialize", _noop_initialize, raising=False)
    try:
        pool = await init_bd_cdp_pool(customer_id="c", zone="z", password="p")
        assert bdp_mod._bd_cdp_pool is pool
    finally:
        bdp_mod._bd_cdp_pool = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_bd_cdp_pool_without_init_is_safe():
    bdp_mod._bd_cdp_pool = None
    try:
        await shutdown_bd_cdp_pool()  # must not raise
    finally:
        bdp_mod._bd_cdp_pool = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_bd_cdp_pool_clears_singleton():
    bdp_mod._bd_cdp_pool = BDCDPPool()
    bdp_mod._bd_cdp_pool._sessions = []
    try:
        await shutdown_bd_cdp_pool()
        assert bdp_mod._bd_cdp_pool is None
    finally:
        bdp_mod._bd_cdp_pool = None


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _async(value):
    return value


async def _noop_initialize(self, *args, **kwargs) -> None:
    return None
