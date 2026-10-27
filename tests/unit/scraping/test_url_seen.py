"""
Unit tests for the URL seen-set Bloom filter.

Covers sizing math, membership, negative results, the exact-URL guarantee,
persistence across instances (shared Redis state), and graceful degradation
when Redis is unavailable.
"""

from __future__ import annotations

import pytest

from searchops.scraping.url_seen import (
    RedisUnavailable,
    SeenSetStats,
    URLSeenSet,
    optimal_bit_array_size,
    optimal_hash_count,
)


class FakeRedis:
    """Minimal async Redis double supporting SETNX and GET."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self.setnx_calls = 0

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def exists(self, key: str) -> int:
        return 1 if key in self._kv else 0

    async def delete(self, *keys) -> int:
        n = 0
        for k in keys:
            # Redis may hand back bytes keys; normalise before comparing.
            sk = k.decode() if isinstance(k, bytes) else k
            n += 1 if self._kv.pop(sk, None) is not None else 0
        return n

    async def scan(self, cursor: int = 0, match: str = "*", count: int = 100):
        import fnmatch

        matched = [k.encode() for k in self._kv if fnmatch.fnmatch(k, match)]
        return 0, matched


class ExplodingRedis:
    async def set(self, *a, **k):
        raise ConnectionError("redis down")

    async def get(self, *a, **k):
        raise ConnectionError("redis down")


# ── Sizing math ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_bit_array_size_matches_textbook_formula():
    # m = -n·ln(p) / (ln 2)^2  ->  958,505,838 bits ≈ 114 MB at 100M URLs, 1% FP
    m = optimal_bit_array_size(100_000_000, 0.01)

    assert m == 958_505_838, f"100M URLs @1% FP = 958,505,838 bits (~114 MB), got {m}"


@pytest.mark.unit
def test_bit_array_size_grows_with_capacity():
    assert optimal_bit_array_size(1_000_000, 0.01) > optimal_bit_array_size(1000, 0.01)


@pytest.mark.unit
def test_bit_array_size_shrinks_as_fp_rate_rises():
    strict = optimal_bit_array_size(1_000_000, 0.001)
    loose = optimal_bit_array_size(1_000_000, 0.05)

    assert strict > loose, "A tighter FP bound needs more bits"


@pytest.mark.unit
def test_hash_count_matches_textbook_formula():
    # k = (m/n)·ln 2, with m=9.6M bits for n=1M URLs -> k ≈ 7
    k = optimal_hash_count(bit_count=9_600_000, expected_urls=1_000_000)

    assert 6 <= k <= 7, f"Expected ~7 hashes, got {k}"


@pytest.mark.unit
def test_hash_count_is_at_least_one():
    assert optimal_hash_count(1000, 10) >= 1


@pytest.mark.unit
def test_sizing_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        optimal_bit_array_size(0, 0.01)
    with pytest.raises(ValueError):
        optimal_bit_array_size(1000, 0.0)
    with pytest.raises(ValueError):
        optimal_bit_array_size(1000, 1.0)


# ── Membership ────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_sighting_is_new():
    s = URLSeenSet(FakeRedis())

    assert await s.seen("https://a.test/1") is False, "A first-time URL is new"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeat_sighting_is_detected():
    s = URLSeenSet(FakeRedis())

    await s.seen("https://a.test/1")

    assert await s.seen("https://a.test/1") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_distinct_urls_are_independent():
    s = URLSeenSet(FakeRedis())

    await s.seen("https://a.test/1")
    await s.seen("https://a.test/2")

    assert await s.seen("https://a.test/3") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normalizes_trivial_url_differences():
    """Trailing slash and fragment should not create a distinct entry."""
    s = URLSeenSet(FakeRedis())

    await s.seen("https://a.test/page")
    await s.seen("https://a.test/page/")

    assert await s.seen("https://a.test/page#section") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_distinguishes_query_strings():
    s = URLSeenSet(FakeRedis())

    await s.seen("https://a.test/p?id=1")

    assert await s.seen("https://a.test/p?id=2") is False, "Query strings are significant"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_distinguishes_scheme_and_host():
    s = URLSeenSet(FakeRedis())

    await s.seen("http://a.test/p")

    assert await s.seen("https://a.test/p") is False
    assert await s.seen("http://b.test/p") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_seen_is_idempotent_after_first_call():
    s = URLSeenSet(FakeRedis())
    await s.seen("https://a.test/1")

    for _ in range(3):
        assert await s.seen("https://a.test/1") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_without_check_marks_seen():
    s = URLSeenSet(FakeRedis())

    await s.add("https://a.test/1")

    assert await s.seen("https://a.test/1") is True


# ── Persistence across instances ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_survives_a_new_instance():
    """The whole point: a restart must not lose the seen-set."""
    redis = FakeRedis()

    first = URLSeenSet(redis)
    await first.seen("https://a.test/1")

    second = URLSeenSet(redis)  # simulates a process restart

    assert await second.seen("https://a.test/1") is True


# ── Failure handling ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_outage_fails_open_by_default():
    """A dead Redis must not block crawling — treat URLs as unseen."""
    s = URLSeenSet(ExplodingRedis(), fail_open=True)

    assert await s.seen("https://a.test/1") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_outage_raises_when_fail_closed():
    s = URLSeenSet(ExplodingRedis(), fail_open=False)

    with pytest.raises(RedisUnavailable):
        await s.seen("https://a.test/1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fail_open_is_recorded():
    s = URLSeenSet(ExplodingRedis(), fail_open=True)
    await s.seen("https://a.test/1")

    assert s.stats.degraded_calls >= 1


# ── Stats ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stats_track_new_and_repeat():
    s = URLSeenSet(FakeRedis())
    await s.seen("https://a.test/1")
    await s.seen("https://a.test/1")
    await s.seen("https://a.test/2")

    assert s.stats.new_count == 2
    assert s.stats.repeat_count == 1


@pytest.mark.unit
def test_stats_type_is_dataclass():
    assert isinstance(SeenSetStats(), SeenSetStats)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reset_clears_tracked_urls():
    redis = FakeRedis()
    s = URLSeenSet(redis)
    await s.seen("https://a.test/1")

    await s.reset()

    assert await s.seen("https://a.test/1") is False, "reset() forgets every URL"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_key_prefix_is_configurable():
    redis = FakeRedis()
    s = URLSeenSet(redis, key_prefix="seen:custom")
    await s.seen("https://a.test/1")

    assert all(k.startswith("seen:custom") for k in redis._kv), (
        f"Keys must use the configured prefix, got {list(redis._kv)}"
    )
