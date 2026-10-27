"""
Tests for pipeline near-duplicate detection and deduplicator selection.

Contract:
  - ``build_deduplicator_from_config`` prefers the persistent backend when a
    Redis URL is present, and falls back to in-memory when it is not — dedup is
    never a hard dependency on infrastructure.
  - The pipeline indexes successful results and tags near-duplicates.
  - A dedup failure must never fail a scrape that already succeeded.
"""

from __future__ import annotations

import pytest

from searchops.scraping.deduplicator import (
    MinHashDeduplicator,
    PersistentMinHashDeduplicator,
    _storage_config_from_url,
    build_deduplicator_from_config,
)

# Must exceed _MIN_DEDUP_CHARS (500) for the pipeline to fingerprint it.
LONG_BODY = (
    "The quick brown fox jumps over the lazy dog while the sun sets behind "
    "the distant mountains and the river flows gently toward the open sea "
    "where the fishermen prepare their nets for another long night of work "
    "and the tide comes in with the changing of the seasons every single year. "
    "Further along the coast the villages begin to stir as the morning light "
    "spreads across the harbour, boats are readied and the market square fills "
    "with traders selling fresh catch, bread, and produce brought down from the "
    "inland farms by cart along the winding roads that connect every settlement."
)
assert len(LONG_BODY) > 500, "fixture must exceed _MIN_DEDUP_CHARS"


# ── build_deduplicator_from_config ───────────────────────────────────────────


class TestDeduplicatorFactory:
    def test_no_redis_url_gives_in_memory(self):
        d = build_deduplicator_from_config(threshold=0.85)
        assert isinstance(d, MinHashDeduplicator)
        assert not isinstance(d, PersistentMinHashDeduplicator)

    def test_prefer_persistent_false_forces_in_memory(self):
        d = build_deduplicator_from_config(redis_url="redis://localhost:6379", prefer_persistent=False)
        assert not isinstance(d, PersistentMinHashDeduplicator)

    def test_redis_url_gives_persistent(self):
        d = build_deduplicator_from_config(redis_url="redis://localhost:6379")
        assert isinstance(d, PersistentMinHashDeduplicator)

    def test_threshold_is_honoured(self):
        d = build_deduplicator_from_config(threshold=0.42)
        assert d.threshold == 0.42

    def test_explicit_storage_config_gives_persistent(self):
        d = build_deduplicator_from_config(storage_config={"type": "redis", "redis": {"host": "h", "port": 1}})
        assert isinstance(d, PersistentMinHashDeduplicator)


class TestStorageConfigFromUrl:
    def test_basic_url(self):
        cfg = _storage_config_from_url("redis://localhost:6379")
        assert cfg["type"] == "redis"
        assert cfg["redis"]["host"] == "localhost"
        assert cfg["redis"]["port"] == 6379

    def test_db_index_from_path(self):
        cfg = _storage_config_from_url("redis://cache.internal:6380/3")
        assert cfg["redis"]["db"] == 3

    def test_password_is_captured(self):
        cfg = _storage_config_from_url("redis://:s3cret@localhost:6379")
        assert cfg["redis"]["password"] == "s3cret"

    def test_tls_scheme_sets_ssl(self):
        cfg = _storage_config_from_url("rediss://secure.internal:6380")
        assert cfg["redis"]["ssl"] is True

    def test_unknown_scheme_returns_empty(self):
        assert _storage_config_from_url("http://localhost:6379") == {}

    def test_missing_port_defaults_to_6379(self):
        cfg = _storage_config_from_url("redis://localhost")
        assert cfg["redis"]["port"] == 6379

    def test_non_numeric_db_is_ignored(self):
        cfg = _storage_config_from_url("redis://localhost:6379/notanumber")
        assert "db" not in cfg["redis"]


# ── Pipeline integration ─────────────────────────────────────────────────────


class _FakeDedup:
    """Records calls so the wiring can be asserted without datasketch."""

    def __init__(self, dup: bool = False) -> None:
        self.dup = dup
        self.indexed: list[str] = []
        self.checked: list[str] = []

    def is_duplicate(self, content: str, threshold: float | None = None):
        self.checked.append(content)
        return (self.dup, ["other-doc"] if self.dup else [])

    def index_document(self, doc_id: str, content: str) -> None:
        self.indexed.append(doc_id)


class _FakeResult:
    def __init__(self, content: str = "", status: int = 200) -> None:
        self.markdown = content
        self.html = ""
        self.status_code = status
        self.url = "https://a.test/page"
        self.final_url = "https://a.test/page"
        self.metadata: dict = {}


class _FakeSeen:
    def __init__(self) -> None:
        # `calls` — not `seen` — so the attribute cannot shadow the method.
        self.calls: list[tuple[str, bool]] = []

    async def seen(self, url: str, force: bool = False) -> bool:
        self.calls.append((url, force))
        return False


class _FakeCache:
    def __init__(self) -> None:
        self.stored: dict = {}

    async def set(self, key, value, ttl_seconds=None) -> None:
        self.stored[key] = value


@pytest.mark.asyncio
class TestPipelineDedupWiring:
    async def _run(self, dedup, result, seen=None, cache=None):
        from searchops.scraping.pipeline import ScrapingPipeline

        pipe = ScrapingPipeline.__new__(ScrapingPipeline)
        pipe.deduplicator = dedup
        pipe.cache = cache
        pipe.url_seen = seen
        await pipe._cache_and_return("k", result)
        return result

    async def test_successful_result_is_indexed(self):
        dedup = _FakeDedup()
        await self._run(dedup, _FakeResult(content=LONG_BODY))
        assert dedup.indexed == ["https://a.test/page"]

    async def test_near_duplicate_is_tagged_in_metadata(self):
        dedup = _FakeDedup(dup=True)
        res = await self._run(dedup, _FakeResult(content=LONG_BODY))
        assert res.metadata["near_duplicate_of"] == ["other-doc"]

    async def test_unique_document_is_not_tagged(self):
        dedup = _FakeDedup(dup=False)
        res = await self._run(dedup, _FakeResult(content=LONG_BODY))
        assert "near_duplicate_of" not in res.metadata

    async def test_short_content_is_not_indexed(self):
        dedup = _FakeDedup()
        await self._run(dedup, _FakeResult(content="too short"))
        assert dedup.indexed == []

    async def test_failed_result_is_not_indexed(self):
        dedup = _FakeDedup()
        await self._run(dedup, _FakeResult(content=LONG_BODY, status=404))
        assert dedup.indexed == []

    async def test_dedup_failure_does_not_fail_the_scrape(self):
        class _Broken:
            def is_duplicate(self, content, threshold=None):
                raise RuntimeError("index corrupted")

            def index_document(self, doc_id, content):
                raise RuntimeError("index corrupted")

        res = _FakeResult(content=LONG_BODY)
        out = await self._run(_Broken(), res)
        assert out is res, "the result must survive a dedup failure"

    async def test_seen_set_and_dedup_both_run(self):
        dedup = _FakeDedup()
        seen = _FakeSeen()
        await self._run(dedup, _FakeResult(content=LONG_BODY), seen=seen)
        assert len(seen.calls) == 1
        assert dedup.indexed

    async def test_works_with_no_seen_set_and_no_cache(self):
        dedup = _FakeDedup()
        res = await self._run(dedup, _FakeResult(content=LONG_BODY))
        assert dedup.indexed == ["https://a.test/page"]
        assert res.status_code == 200


# ── Real detection through the wired path ────────────────────────────────────


class TestRealDetection:
    def test_real_deduplicator_detects_near_duplicate(self):
        """End-to-end: a second, reworded copy is caught by the real engine."""
        d = MinHashDeduplicator(threshold=0.5)
        d.index_document("first", LONG_BODY)

        reworded = LONG_BODY.replace("lazy dog", "sleepy hound").replace("gently", "softly")
        is_dup, matched = d.is_duplicate(reworded)
        assert is_dup is True
        assert "first" in matched

    def test_unrelated_content_is_not_flagged(self):
        d = MinHashDeduplicator(threshold=0.5)
        d.index_document("first", LONG_BODY)
        other = "Quantum computing leverages superposition and entanglement. " * 6
        assert d.is_duplicate(other)[0] is False
