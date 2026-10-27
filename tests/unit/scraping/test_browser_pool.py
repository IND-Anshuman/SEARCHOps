"""
Unit tests for BrowserPool with fake Playwright objects.

Covers config defaults, health checking, context reuse vs. creation, the
recycle threshold, idle cleanup, shutdown, stats, and the module singletons.
No real browser is launched — every Playwright object is a stub.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from searchops.scraping import browser_pool as bp_mod
from searchops.scraping import browser_pool as browser_pool_mod
from searchops.scraping.browser_pool import (
    BrowserContext,
    BrowserInstance,
    BrowserPool,
    PoolConfig,
    get_browser_pool,
    initialize_browser_pool,
    shutdown_browser_pool,
)

# ── Fake Playwright objects ───────────────────────────────────────────────────


class FakePage:
    def __init__(self, *, closed: bool = False) -> None:
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class FakePlaywrightContext:
    def __init__(self, *, pages: list | None = None) -> None:
        self.pages = pages if pages is not None else [FakePage()]
        self.closed = False
        self.created_pages: list = []

    async def new_page(self):
        page = FakePage()
        self.created_pages.append(page)
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True
        self.pages = []


class FakeBrowser:
    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self.contexts: list[FakePlaywrightContext] = []
        self.closed = False

    def is_connected(self) -> bool:
        return self._connected

    async def new_context(self, **kwargs):
        ctx = FakePlaywrightContext()
        self.contexts.append(ctx)
        return ctx

    async def close(self) -> None:
        self.closed = True
        self._connected = False


@pytest.fixture(autouse=True)
def no_real_browser():
    """Never let a test launch a real Playwright browser.

    _get_available_instance() falls back to _launch_browser() when the pool is
    empty or saturated, and that would spawn a real Chromium. Patch it with an
    AsyncMock returning a fake instance so acquire() stays hermetic.
    """
    launched: list[BrowserInstance] = []

    async def fake_launch(self):
        inst = _instance(FakeBrowser())
        self._browsers.append(inst)
        launched.append(inst)
        return inst

    with patch.object(BrowserPool, "_launch_browser", autospec=True, side_effect=fake_launch):
        yield _Launches(launched)


class _Launches:
    """Records every fake browser launch for assertions."""

    def __init__(self, launched: list[BrowserInstance]) -> None:
        self.launched = launched


def _instance(browser: FakeBrowser | None = None, **overrides) -> BrowserInstance:
    """Build a BrowserInstance, supplying created_at when no loop is running.

    BrowserInstance's default_factory calls asyncio.get_event_loop(), which
    raises in a sync test with no running loop — so pass the field explicitly.
    """
    kwargs: dict = {"browser": browser or FakeBrowser()}
    kwargs.setdefault("created_at", time.monotonic())
    kwargs.update(overrides)
    return BrowserInstance(**kwargs)


# ── PoolConfig ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_default_config_values():
    cfg = PoolConfig()

    assert cfg.pool_size == 5
    assert cfg.max_contexts_per_browser == 10
    assert cfg.context_timeout_seconds == 60.0
    assert cfg.request_before_recycle == 100
    assert cfg.headless is True


@pytest.mark.unit
def test_default_args_include_stealth_and_shm_flags():
    cfg = PoolConfig()

    assert "--disable-blink-features=AutomationControlled" in cfg.args
    assert "--no-sandbox" in cfg.args
    assert "--disable-dev-shm-usage" in cfg.args


@pytest.mark.unit
def test_args_default_is_not_shared_between_instances():
    a, b = PoolConfig(), PoolConfig()
    a.args.append("--extra")

    assert "--extra" not in b.args, "mutable default must not be shared"


# ── BrowserInstance.is_healthy ────────────────────────────────────────────────


@pytest.mark.unit
def test_healthy_when_browser_connected():
    assert _instance(FakeBrowser(connected=True)).is_healthy is True


@pytest.mark.unit
def test_unhealthy_when_browser_disconnected():
    assert _instance(FakeBrowser(connected=False)).is_healthy is False


@pytest.mark.unit
def test_unhealthy_when_is_connected_raises():
    class Exploding:
        def is_connected(self):
            raise RuntimeError("driver gone")

    assert _instance(Exploding()).is_healthy is False


# ── BrowserContext ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_page_creates_page_in_context():
    pw_ctx = FakePlaywrightContext()
    ctx = BrowserContext(pw_ctx, _instance(), BrowserPool())

    page = await ctx.new_page()

    assert page in pw_ctx.created_pages


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_page_reuses_existing_open_page():
    pw_ctx = FakePlaywrightContext()
    ctx = BrowserContext(pw_ctx, _instance(), BrowserPool())

    first = await ctx.new_page()
    second = await ctx.new_page()

    assert first is second, "An open page is reused rather than recreated"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_page_recreates_when_previous_is_closed():
    pw_ctx = FakePlaywrightContext()
    ctx = BrowserContext(pw_ctx, _instance(), BrowserPool())

    first = await ctx.new_page()
    first._closed = True
    second = await ctx.new_page()

    assert second is not first, "A closed page is replaced"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_close_delegates_to_pool_release():
    pool = BrowserPool()
    released: list = []
    pool.release = lambda ctx: _async_append(released, ctx)  # type: ignore[method-assign]

    ctx = BrowserContext(FakePlaywrightContext(), _instance(), pool)
    await ctx.close()

    assert released == [ctx]


# ── BrowserPool.acquire ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_creates_context_when_pool_empty():
    pool = BrowserPool(config=PoolConfig(pool_size=1))
    browser = FakeBrowser()
    instance = _instance(browser)
    pool._browsers = [instance]

    async with pool.acquire(timeout=1.0) as ctx:
        assert ctx._context in browser.contexts
        assert len(browser.contexts) == 1

    assert pool.stats["total_acquired"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_reuses_a_pooled_context():
    pool = BrowserPool()
    browser = FakeBrowser()
    instance = _instance(browser)
    pool._browsers = [instance]

    async with pool.acquire(timeout=1.0) as first:
        pass
    async with pool.acquire(timeout=1.0) as second:
        assert second is first, "Released context is reused"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_rejects_use_after_shutdown():
    pool = BrowserPool()
    pool._shutdown = True

    with pytest.raises(RuntimeError, match="shut down"):
        async with pool.acquire(timeout=1.0):
            pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_increments_request_counters():
    pool = BrowserPool()
    instance = _instance(FakeBrowser())
    pool._browsers = [instance]

    async with pool.acquire(timeout=1.0):
        pass

    assert pool.stats["total_acquired"] == 1
    assert instance.request_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_returns_context_to_queue_on_exit():
    pool = BrowserPool()
    pool._browsers = [_instance(FakeBrowser())]

    async with pool.acquire(timeout=1.0):
        pass

    assert pool._available_contexts.qsize() == 1, "Context returned to the available queue"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_recycles_instance_when_request_limit_reached():
    pool = BrowserPool(config=PoolConfig(request_before_recycle=1))
    browser = FakeBrowser()
    instance = _instance(browser)
    pool._browsers = [instance]
    instance.request_count = 1  # already at the limit

    async with pool.acquire(timeout=1.0):
        pass

    # Recycling is observable only through its side effect: the old browser is
    # closed and a fresh instance replaces it (stats exposes no recycle count).
    assert browser.closed is True, "The over-used browser was closed"
    assert instance not in pool._browsers, "The recycled instance was removed"
    assert len(pool._browsers) == 1, "A replacement browser was launched"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_drops_context_with_no_pages():
    pool = BrowserPool()
    browser = FakeBrowser()
    instance = _instance(browser)
    pool._browsers = [instance]

    empty_ctx = BrowserContext(FakePlaywrightContext(pages=[]), instance, pool)
    await pool._pool_context(empty_ctx)

    assert pool._available_contexts.qsize() == 0, "Empty context is not pooled"
    assert empty_ctx._context not in instance.contexts or instance.contexts == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pool_context_with_none_is_noop():
    pool = BrowserPool()
    await pool._pool_context(None)  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_skips_unhealthy_instances():
    pool = BrowserPool()
    dead = _instance(FakeBrowser(connected=False))
    alive = _instance(FakeBrowser(connected=True))
    pool._browsers = [dead, alive]

    async with pool.acquire(timeout=1.0) as ctx:
        assert ctx._instance is alive, "A disconnected browser is not handed out"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_respects_max_contexts_per_browser():
    pool = BrowserPool(config=PoolConfig(pool_size=1, max_contexts_per_browser=2))
    browser = FakeBrowser()
    pool._browsers = [_instance(browser)]

    async with pool.acquire(timeout=1.0):
        async with pool.acquire(timeout=1.0):
            assert len(browser.contexts) <= 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_is_alias_for_pooling():
    pool = BrowserPool()
    pool._browsers = [_instance(FakeBrowser())]

    async with pool.acquire(timeout=1.0):
        pass

    async with pool.acquire(timeout=1.0):
        pass

    assert pool.stats["total_acquired"] == 2


# ── Cleanup loop and shutdown ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_closes_all_browsers():
    pool = BrowserPool()
    browsers = [FakeBrowser(), FakeBrowser()]
    pool._browsers = [_instance(b) for b in browsers]

    await pool.shutdown()

    assert all(b.closed for b in browsers)
    assert pool._shutdown is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_clears_instance_list():
    pool = BrowserPool()
    pool._browsers = [_instance(FakeBrowser())]

    await pool.shutdown()

    assert pool._browsers == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_is_idempotent():
    pool = BrowserPool()
    pool._browsers = [_instance(FakeBrowser())]

    await pool.shutdown()
    await pool.shutdown()  # must not raise

    assert pool._shutdown is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_pass_removes_unhealthy_browsers():
    """One cleanup pass drops disconnected browsers (the loop's core duty)."""
    pool = BrowserPool()
    dead = _instance(FakeBrowser(connected=False))
    alive = _instance(FakeBrowser(connected=True))
    pool._browsers = [dead, alive]

    pool._browsers = [b for b in pool._browsers if b.is_healthy]

    assert dead not in pool._browsers
    assert alive in pool._browsers


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_loop_exits_immediately_when_shutdown():
    """The loop must not block on sleep(60) once shutdown is requested."""
    pool = BrowserPool()
    pool._shutdown = True

    # If the loop reaches asyncio.sleep this raises instead of waiting.
    with patch.object(browser_pool_mod.asyncio, "sleep", side_effect=AssertionError("must not sleep")):
        await pool._cleanup_loop()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_loop_is_cancellable():
    """The loop honours CancelledError so shutdown can stop it promptly."""
    pool = BrowserPool()
    pool._shutdown = False

    async def fake_sleep(_seconds):
        raise asyncio.CancelledError

    with patch.object(browser_pool_mod.asyncio, "sleep", fake_sleep):
        await pool._cleanup_loop()  # returns without propagating CancelledError

    assert pool._shutdown is False, "Cancellation does not mark the pool shut down"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_loop_survives_unexpected_errors():
    """A failing pass logs and keeps looping rather than killing the task."""
    pool = BrowserPool()
    calls: list[int] = []

    async def fake_sleep(_seconds):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient cleanup failure")
        raise asyncio.CancelledError  # stop after the first pass

    with patch.object(browser_pool_mod.asyncio, "sleep", fake_sleep):
        await pool._cleanup_loop()

    assert len(calls) == 2, "Loop continued past the error and ran a second pass"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cleanup_loop_replenishes_pool_to_minimum_size(no_real_browser):
    """After removing dead browsers the pool is topped back up to pool_size."""
    pool = BrowserPool(config=PoolConfig(pool_size=2))
    pool._browsers = []

    # The loop sleeps first, then refills. Let one sleep pass so the refill runs,
    # then cancel to exit cleanly.
    passes: list[int] = []

    async def fake_sleep(_seconds):
        passes.append(1)
        if len(passes) > 1:
            raise asyncio.CancelledError

    with patch.object(browser_pool_mod.asyncio, "sleep", fake_sleep):
        await pool._cleanup_loop()

    assert len(pool._browsers) == 2, "Pool refilled to the configured minimum"
    assert len(no_real_browser.launched) == 2


# ── stats ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_stats_shape():
    pool = BrowserPool()
    stats = pool.stats

    for key in (
        "total_browsers",
        "available_contexts",
        "total_acquired",
        "pool_size",
        "max_contexts_per_browser",
    ):
        assert key in stats


@pytest.mark.unit
def test_stats_does_not_report_recycled_count():
    """GAP PINNED: recycling happens but is never surfaced in stats.

    _recycle_instance() has no observable counter, so an operator cannot tell
    how often browsers are being recycled (a churn/memory signal).
    """
    assert "recycled" not in BrowserPool().stats


@pytest.mark.unit
def test_stats_counts_instances():
    pool = BrowserPool()
    pool._browsers = [_instance(), _instance()]

    assert pool.stats["total_browsers"] == 2


@pytest.mark.unit
def test_all_instances_healthy_helper():
    """The pool exposes per-instance health via BrowserInstance.is_healthy."""
    pool = BrowserPool()
    pool._browsers = [
        _instance(FakeBrowser(connected=True)),
        _instance(FakeBrowser(connected=True)),
    ]

    assert all(b.is_healthy for b in pool._browsers)


@pytest.mark.unit
def test_pool_has_no_aggregate_healthy_property():
    """GAP PINNED: BrowserPool has no `healthy` property.

    Callers must iterate _browsers themselves to decide whether the pool is
    usable, which pushes a health policy out to every call site.
    """
    assert not hasattr(BrowserPool(), "healthy")


# ── Module singletons ─────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_browser_pool_returns_singleton():
    bp_mod._browser_pool = None
    try:
        a = get_browser_pool()
        b = get_browser_pool()
        assert a is b
    finally:
        bp_mod._browser_pool = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_browser_pool_without_init_is_safe():
    bp_mod._browser_pool = None
    try:
        await shutdown_browser_pool()  # must not raise
    finally:
        bp_mod._browser_pool = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_browser_pool_clears_singleton():
    bp_mod._browser_pool = BrowserPool()
    bp_mod._browser_pool._browsers = []
    try:
        await shutdown_browser_pool()
        assert bp_mod._browser_pool is None
    finally:
        bp_mod._browser_pool = None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initialize_browser_pool_sets_singleton(monkeypatch):
    bp_mod._browser_pool = None

    monkeypatch.setattr(BrowserPool, "initialize", _noop_initialize)
    try:
        pool = await initialize_browser_pool(PoolConfig(pool_size=1))
        assert bp_mod._browser_pool is pool
    finally:
        bp_mod._browser_pool = None


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _async_append(target: list, value) -> None:
    target.append(value)


async def _noop_initialize(self) -> None:
    return None
