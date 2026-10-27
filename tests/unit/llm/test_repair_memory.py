"""
Tests for per-domain repair memory.

Covers learning (preferred tier after repeated repairs), bounded memory (LRU
eviction under the domain cap), thread safety of concurrent recording, and the
metric contract — the numbers that make the self-healing loop's value
measurable rather than asserted.
"""

from __future__ import annotations

import threading

import pytest

from searchops.llm.json_repair import (
    TIER_BALANCED_SLICE,
    TIER_EXACT,
    TIER_FENCE_STRIP,
    TIER_SPAN_SALVAGE,
    TIER_STRUCTURAL_REPAIR,
)
from searchops.llm.repair_memory import DomainRepairMemory, RepairMemory, get_repair_memory

# ── DomainRepairMemory ───────────────────────────────────────────────────────


class TestDomainRepairMemory:
    def test_initial_state(self):
        m = DomainRepairMemory()
        assert m.last_successful_tier == 0
        assert m.successes == 0
        assert m.failures == 0

    def test_note_success_updates_preferred_tier(self):
        m = DomainRepairMemory()
        m.note_success(TIER_STRUCTURAL_REPAIR)
        assert m.last_successful_tier == TIER_STRUCTURAL_REPAIR
        assert m.successes == 1

    def test_note_success_accumulates_histogram(self):
        m = DomainRepairMemory()
        m.note_success(TIER_FENCE_STRIP)
        m.note_success(TIER_FENCE_STRIP)
        m.note_success(TIER_STRUCTURAL_REPAIR)
        assert m.tier_counts == {TIER_FENCE_STRIP: 2, TIER_STRUCTURAL_REPAIR: 1}

    def test_note_failure_does_not_change_preferred_tier(self):
        m = DomainRepairMemory()
        m.note_success(TIER_FENCE_STRIP)
        m.note_failure()
        assert m.last_successful_tier == TIER_FENCE_STRIP
        assert m.failures == 1

    def test_last_seen_updates(self):
        m = DomainRepairMemory()
        before = m.last_seen
        m.note_success(TIER_EXACT)
        assert m.last_seen >= before


# ── RepairMemory: learning ───────────────────────────────────────────────────


class TestRepairMemoryLearning:
    def test_unknown_domain_has_no_preference(self):
        mem = RepairMemory()
        assert mem.preferred_tier("fresh.example.com") is None
        assert mem.has_seen("fresh.example.com") is False

    def test_records_preferred_tier_after_success(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_STRUCTURAL_REPAIR, recovered=True)
        assert mem.preferred_tier("a.com") == TIER_STRUCTURAL_REPAIR
        assert mem.has_seen("a.com") is True

    def test_latest_success_wins(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_STRUCTURAL_REPAIR, recovered=True)
        mem.record("a.com", TIER_BALANCED_SLICE, recovered=True)
        assert mem.preferred_tier("a.com") == TIER_BALANCED_SLICE

    def test_failure_alone_does_not_create_preference(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_SPAN_SALVAGE, recovered=False)
        # The domain is known, but no tier has ever worked for it.
        assert mem.has_seen("a.com") is True
        stats = mem.domain_stats("a.com")
        assert stats is not None
        assert stats["last_successful_tier"] == 0
        assert stats["failures"] == 1

    def test_domains_are_independent(self):
        mem = RepairMemory()
        mem.record("good.com", TIER_FENCE_STRIP, recovered=True)
        mem.record("bad.com", TIER_SPAN_SALVAGE, recovered=False)
        assert mem.preferred_tier("good.com") == TIER_FENCE_STRIP
        assert mem.preferred_tier("bad.com") == 0

    def test_domain_stats_none_for_unknown(self):
        mem = RepairMemory()
        assert mem.domain_stats("never.example.com") is None

    def test_domain_stats_structure(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_STRUCTURAL_REPAIR, recovered=True)
        mem.record("a.com", TIER_SPAN_SALVAGE, recovered=False)

        stats = mem.domain_stats("a.com")
        assert stats is not None
        assert stats["last_successful_tier"] == TIER_STRUCTURAL_REPAIR
        assert stats["successes"] == 1
        assert stats["failures"] == 1
        assert stats["recovery_rate"] == 0.5
        assert TIER_STRUCTURAL_REPAIR in stats["tier_counts"]


# ── Bounded memory / eviction ────────────────────────────────────────────────


class TestBoundedMemory:
    def test_evicts_least_recently_seen_over_cap(self):
        mem = RepairMemory(max_domains=3)
        for i in range(3):
            mem.record(f"d{i}.com", TIER_EXACT, recovered=True)

        # Touch d0 so it is no longer the least recently seen.
        assert mem.preferred_tier("d0.com") == TIER_EXACT

        # Adding a 4th domain must evict d1 (least recently seen).
        mem.record("d3.com", TIER_EXACT, recovered=True)

        assert mem.domain_stats("d1.com") is None
        assert mem.domain_stats("d0.com") is not None
        assert mem.domain_stats("d3.com") is not None

    def test_tracks_eviction_count(self):
        mem = RepairMemory(max_domains=2)
        for i in range(5):
            mem.record(f"d{i}.com", TIER_EXACT, recovered=True)
        snapshot = mem.snapshot()
        assert snapshot["evictions"] == 3
        assert snapshot["domains_tracked"] == 2
        assert snapshot["max_domains"] == 2

    def test_memory_cannot_grow_unbounded(self):
        """A crawl over a million hosts must not grow this map."""
        mem = RepairMemory(max_domains=100)
        for i in range(5000):
            mem.record(f"host{i}.com", TIER_EXACT, recovered=True)
        snapshot = mem.snapshot()
        assert snapshot["domains_tracked"] == 100
        assert snapshot["evictions"] == 4900


# ── Metrics contract ─────────────────────────────────────────────────────────


class TestMetrics:
    def test_empty_snapshot_has_zero_rates_not_none(self):
        mem = RepairMemory()
        s = mem.snapshot()
        assert s["attempts"] == 0
        assert s["recovery_rate"] == 0.0
        assert s["avg_tiers_tried"] == 0.0

    def test_recovery_rate_counts_recovered(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_FENCE_STRIP, recovered=True)
        mem.record("b.com", TIER_SPAN_SALVAGE, recovered=False)
        s = mem.snapshot()
        assert s["attempts"] == 2
        assert s["recovered"] == 1
        assert s["unrecovered"] == 1
        assert s["recovery_rate"] == 0.5

    def test_avg_tiers_tried_is_the_ladder_depth(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_EXACT, recovered=True)  # depth 0
        mem.record("b.com", TIER_STRUCTURAL_REPAIR, recovered=True)  # depth 3
        assert mem.snapshot()["avg_tiers_tried"] == pytest.approx(1.5)

    def test_snapshot_contains_every_metric(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_EXACT, recovered=True)
        for key in (
            "attempts",
            "recovered",
            "unrecovered",
            "recovery_rate",
            "avg_tiers_tried",
            "domains_tracked",
            "evictions",
            "max_domains",
        ):
            assert key in mem.snapshot()

    def test_reset_clears_everything(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_EXACT, recovered=True)
        mem.reset()
        s = mem.snapshot()
        assert s["attempts"] == 0
        assert s["domains_tracked"] == 0
        assert mem.preferred_tier("a.com") is None


# ── Thread safety ────────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_records_are_all_counted(self):
        mem = RepairMemory(max_domains=10_000)
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(200):
                    mem.record(f"d{n}.com", TIER_EXACT, recovered=(i % 2 == 0))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        s = mem.snapshot()
        assert s["attempts"] == 1600
        assert s["recovered"] + s["unrecovered"] == 1600

    def test_concurrent_preferred_tier_reads_do_not_raise(self):
        mem = RepairMemory()
        mem.record("a.com", TIER_EXACT, recovered=True)
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(200):
                    mem.preferred_tier("a.com")
                    mem.domain_stats("a.com")
                    mem.snapshot()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ── Singleton ────────────────────────────────────────────────────────────────


class TestSingleton:
    def test_global_memory_is_shared(self):
        a = get_repair_memory()
        b = get_repair_memory()
        assert a is b

    def test_global_memory_records_visible_across_calls(self):
        mem = get_repair_memory()
        mem.reset()
        mem.record("singleton-test.example.com", TIER_FENCE_STRIP, recovered=True)
        assert get_repair_memory().preferred_tier("singleton-test.example.com") == TIER_FENCE_STRIP
        mem.reset()
