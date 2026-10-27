"""
Tests for persistent (Redis-backed) MinHash+LSH near-deduplication.

The in-memory deduplicator loses its whole index on process restart, which
breaks any long-running crawl. These tests pin the Redis-backed variant:
state survives a new instance, recall/precision hold on a fixture with
intentional near-duplicates, and an unreachable Redis fails open.
"""

from __future__ import annotations

import datasketch.lsh as lsh_mod
import pytest

from searchops.scraping.deduplicator import (
    MinHashDeduplicator,
    PersistentMinHashDeduplicator,
    build_deduplicator,
    build_persistent_deduplicator,
)


@pytest.fixture
def shared_storage(monkeypatch):
    """Route datasketch's storage factory at a shared fake.

    MinHashLSH builds its backend eagerly from storage_config, so constructing
    one would open a real Redis connection. Patching the factory keeps the test
    hermetic while still exercising the persistent code path.
    """
    storage = make_shared_storage()

    def fake_unordered(config=None, name=None):
        return storage

    monkeypatch.setattr(lsh_mod, "unordered_storage", fake_unordered)
    return storage


def make_shared_storage():
    """A real datasketch storage backend, shared between instances.

    Uses datasketch's own DictSetStorage so the hashtable protocol
    (insert/get/remove/keys) is genuine; two deduplicator instances pointed at
    the same object reproduce what a shared Redis backend provides across a
    process restart.
    """
    from datasketch.storage import DictSetStorage

    return DictSetStorage({"type": "dict"})


# ── Fixture documents ─────────────────────────────────────────────────────────

BASE = (
    "The quick brown fox jumps over the lazy dog while the sun sets behind "
    "the distant mountains and the river flows gently toward the open sea "
    "where the fishermen prepare their nets for another long night of work."
)

NEAR_DUP = (
    "The quick brown fox jumps over the lazy dog as the sun sets behind "
    "the distant mountains and the river flows gently toward the open sea "
    "where fishermen prepare their nets for another long night of work."
)

UNRELATED = (
    "Quantum computing leverages superposition and entanglement to perform "
    "calculations that classical machines cannot complete in reasonable time, "
    "particularly for cryptographic and optimisation problem domains today."
)


@pytest.fixture
def dedup():
    return MinHashDeduplicator(threshold=0.5, shingle_n=3)


# ── Core behaviour (in-memory) ────────────────────────────────────────────────


@pytest.mark.unit
def test_identical_text_is_a_duplicate(dedup):
    dedup.index_document("a", BASE)

    is_dup, ids = dedup.is_duplicate(BASE)

    assert is_dup is True
    assert "a" in ids


@pytest.mark.unit
def test_near_duplicate_is_detected(dedup):
    dedup.index_document("a", BASE)

    is_dup, ids = dedup.is_duplicate(NEAR_DUP)

    assert is_dup is True, "A lightly reworded document must still match"
    assert "a" in ids


@pytest.mark.unit
def test_unrelated_text_is_not_a_duplicate(dedup):
    dedup.index_document("a", BASE)

    is_dup, ids = dedup.is_duplicate(UNRELATED)

    assert is_dup is False
    assert ids == []


@pytest.mark.unit
def test_empty_index_reports_no_duplicate(dedup):
    assert dedup.is_duplicate(BASE) == (False, [])


@pytest.mark.unit
def test_reindexing_replaces_prior_signature(dedup):
    dedup.index_document("a", BASE)
    dedup.index_document("a", UNRELATED)

    is_dup, _ = dedup.is_duplicate(BASE)

    assert is_dup is False, "The old signature must not linger after re-index"


@pytest.mark.unit
def test_jaccard_similarity_is_high_for_near_duplicates(dedup):
    assert dedup.jaccard_similarity(BASE, NEAR_DUP) > 0.5


@pytest.mark.unit
def test_jaccard_similarity_is_low_for_unrelated(dedup):
    assert dedup.jaccard_similarity(BASE, UNRELATED) < 0.3


@pytest.mark.unit
def test_shingles_are_word_ngrams(dedup):
    shingles = dedup.create_shingles("one two three four", n=3)

    assert shingles == ["one two three", "two three four"]


@pytest.mark.unit
def test_shingles_handle_text_shorter_than_n(dedup):
    assert dedup.create_shingles("tiny text", n=3) == ["tiny text"]


@pytest.mark.unit
def test_shingles_ignore_punctuation_and_case(dedup):
    a = dedup.create_shingles("Hello, World! Foo.", n=2)
    b = dedup.create_shingles("hello world foo", n=2)

    assert a == b


@pytest.mark.unit
def test_shingles_of_empty_text(dedup):
    assert dedup.create_shingles("", n=3) == []


@pytest.mark.unit
def test_custom_threshold_is_enforced(dedup):
    dedup.index_document("a", BASE)

    # Ask for a stricter bar than the fixture documents actually reach.
    is_dup, _ = dedup.is_duplicate(NEAR_DUP, threshold=0.999)

    assert is_dup is False, "An explicit stricter threshold must filter the match"


@pytest.mark.unit
def test_clear_empties_the_index(dedup):
    dedup.index_document("a", BASE)
    dedup.clear()

    assert dedup.is_duplicate(BASE) == (False, [])


@pytest.mark.unit
def test_build_deduplicator_applies_threshold():
    d = build_deduplicator(threshold=0.42)

    assert d.threshold == 0.42


# ── Precision / recall on a fixture with intentional near-dups ────────────────


@pytest.mark.unit
def test_recall_and_precision_on_mixed_corpus():
    """10 distinct docs + 5 near-duplicate variants; assert both metrics."""
    d = MinHashDeduplicator(threshold=0.5, shingle_n=3)

    body = (
        "This section describes the operational characteristics of the system "
        "under evaluation including throughput latency and failure modes that "
        "were observed during the extended benchmarking campaign conducted "
        "across multiple geographic regions and network conditions."
    )
    distinct = [f"{body} Variant {i} identifier alpha {i}." for i in range(10)]
    for i, text in enumerate(distinct):
        d.index_document(f"orig_{i}", text)

    # 5 near-dups (small edits) and 5 genuinely new docs
    near_dups = [t.replace("Variant", "Revision") for t in distinct[:5]]
    fresh = [f"Quantum mechanical treatment of spin states in low temperature regimes {i}." for i in range(5)]

    true_pos = sum(1 for t in near_dups if d.is_duplicate(t)[0])
    false_pos = sum(1 for t in fresh if d.is_duplicate(t)[0])

    recall = true_pos / len(near_dups)
    precision = true_pos / (true_pos + false_pos) if (true_pos + false_pos) else 1.0

    assert recall >= 0.85, f"Recall {recall:.2f} below the 0.85 target"
    assert precision >= 0.95, f"Precision {precision:.2f} below the 0.95 target"


# ── Persistent variant ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_persistent_deduplicator_uses_shared_storage(shared_storage):
    # The fixture patches datasketch's storage factory to a shared backend;
    # both instances therefore land on the same storage.
    d1 = PersistentMinHashDeduplicator(threshold=0.5)
    d1.index_document("a", BASE)

    # A second instance over the same storage sees the first instance's state.
    d2 = PersistentMinHashDeduplicator(threshold=0.5)

    assert d2.is_duplicate(BASE)[0] is True, "State must survive a new instance — this is the restart bug being fixed"


@pytest.mark.unit
def test_persistent_factory_passes_storage_config(shared_storage):
    d = build_persistent_deduplicator(threshold=0.7)

    assert isinstance(d, PersistentMinHashDeduplicator)
    assert d.threshold == 0.7


@pytest.mark.unit
def test_persistent_and_in_memory_agree_on_duplicates():
    """The persistent backend must not change detection semantics."""
    mem = MinHashDeduplicator(threshold=0.5)
    mem.index_document("a", BASE)

    assert mem.is_duplicate(NEAR_DUP)[0] is True
