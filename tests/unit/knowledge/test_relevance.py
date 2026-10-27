"""Tests for the deterministic BM25 relevance filter.

Why this stage exists
---------------------
Corpus mode fetches many pages, and most of them are off-topic for the query.
Before this, every fetched page went to the LLM — so the crawl's volume was
paid for in tokens. BM25 scores each page against the query for **free**, so
off-topic pages are dropped before any LLM call ever sees them.

Design decisions these tests pin:

* **Deterministic and dependency-free.** No embeddings, no network, no model.
  Same input always yields the same scores, which is what makes the funnel
  auditable.
* **Stopwords are removed**, so "what is a vector database" ranks on *vector*
  and *database*, not on *what*/*is*.
* **Threshold + top-K both apply.** A page below the threshold is dropped even
  if it is in the top K; the top-K caps how many survive regardless.
* **An empty or stopword-only query returns everything unchanged** rather
  than silently dropping the whole corpus.
* Precision mode is unaffected: the caller only invokes this when the plan
  says to.
"""

from __future__ import annotations

from searchops.knowledge.evaluation.relevance import (
    BM25Scorer,
    RelevanceVerdict,
    filter_by_relevance,
)

QUERY = "vector database approximate nearest neighbour search"

ON_TOPIC = """
Vector databases store high-dimensional embeddings and support approximate
nearest neighbour search over them. HNSW builds a navigable small-world graph
so retrieval stays fast as the collection grows. Qdrant and Milvus both
implement this index, trading recall against latency.
"""

OFF_TOPIC = """
The history of the bicycle begins in nineteenth century Europe. Early designs
used a wooden frame with iron wheels, and pedals were not added until much
later. Cycling clubs formed across France and Italy, and racing became a
popular spectator sport by the 1880s.
"""

PARTIAL = """
Databases come in many forms. Relational engines such as Postgres store rows
in tables and join them with SQL. This article surveys indexing strategies,
including B-trees and hash indexes, and when each is appropriate.
"""


class TestBM25Scorer:
    def test_on_topic_scores_above_off_topic(self) -> None:
        scorer = BM25Scorer(QUERY)
        assert scorer.score(ON_TOPIC) > scorer.score(OFF_TOPIC)

    def test_unrelated_page_scores_near_zero(self) -> None:
        scorer = BM25Scorer(QUERY)
        assert scorer.score(OFF_TOPIC) < 0.5

    def test_stopwords_do_not_drive_the_score(self) -> None:
        """'what is the' must not make a page look relevant."""
        scorer = BM25Scorer(QUERY)
        padded = "what is the " * 50 + "unrelated content about weather"
        assert scorer.score(padded) < scorer.score(ON_TOPIC)

    def test_scoring_is_deterministic(self) -> None:
        a = BM25Scorer(QUERY).score(ON_TOPIC)
        b = BM25Scorer(QUERY).score(ON_TOPIC)
        assert a == b

    def test_empty_document_scores_zero(self) -> None:
        assert BM25Scorer(QUERY).score("") == 0.0

    def test_idf_rewards_rare_terms(self) -> None:
        """A page matching a rare query term beats one matching only common ones."""
        scorer = BM25Scorer("qdrant postgres")
        rare = "Qdrant stores vectors and supports filtered search."
        common = "A database is a system for storing and organising data."
        assert scorer.score(rare) > scorer.score(common)

    def test_term_frequency_saturation(self) -> None:
        """Repeating a term many times must not let a page run away."""
        scorer = BM25Scorer("vector")
        once = "A vector is a quantity with magnitude and direction, used widely."
        spam = ("vector " * 200) + "unrelated filler text about gardening tools."
        assert scorer.score(spam) < scorer.score(once) * 3


class TestFilterByRelevance:
    def _pages(self) -> list[dict[str, object]]:
        return [
            {"url": "https://a.com/on", "content": ON_TOPIC},
            {"url": "https://a.com/off", "content": OFF_TOPIC},
            {"url": "https://a.com/partial", "content": PARTIAL},
        ]

    def test_keeps_relevant_pages(self) -> None:
        kept, _dropped, _stats = filter_by_relevance(self._pages(), QUERY)
        urls = {p["url"] for p in kept}
        assert "https://a.com/on" in urls

    def test_drops_off_topic_pages(self) -> None:
        kept, dropped, _stats = filter_by_relevance(self._pages(), QUERY)
        urls = {p["url"] for p in kept}
        assert "https://a.com/off" not in urls
        assert "https://a.com/off" in {d["url"] for d in dropped}

    def test_reports_counts(self) -> None:
        _kept, _dropped, stats = filter_by_relevance(self._pages(), QUERY)
        assert stats["pages_kept"] + stats["pages_dropped"] == 3
        assert stats["pages_kept"] >= 1

    def test_verdict_is_attached_to_kept_pages(self) -> None:
        kept, _dropped, _stats = filter_by_relevance(self._pages(), QUERY)
        for page in kept:
            assert page["relevance"] == RelevanceVerdict.KEPT.value
            assert isinstance(page["relevance_score"], float)

    def test_dropped_page_records_why(self) -> None:
        _kept, dropped, _stats = filter_by_relevance(self._pages(), QUERY)
        for page in dropped:
            assert page["relevance"] == RelevanceVerdict.DROPPED.value

    def test_top_k_caps_the_survivors(self) -> None:
        pages = [{"url": f"https://a.com/{i}", "content": ON_TOPIC} for i in range(10)]
        kept, _dropped, _stats = filter_by_relevance(pages, QUERY, top_k=3)
        assert len(kept) == 3

    def test_highest_scores_survive_the_cap(self) -> None:
        """When truncated, the best pages must be the ones kept."""
        pages = [{"url": "https://a.com/on", "content": ON_TOPIC}]
        pages += [{"url": f"https://a.com/weak{i}", "content": PARTIAL} for i in range(5)]
        kept, _dropped, _stats = filter_by_relevance(pages, QUERY, top_k=2)
        assert any(p["url"] == "https://a.com/on" for p in kept)

    def test_empty_query_keeps_everything(self) -> None:
        """A stopword-only query must not silently discard the corpus."""
        pages = self._pages()
        kept, dropped, stats = filter_by_relevance(pages, "what is the")
        assert len(kept) == 3
        assert dropped == []
        assert stats["degraded"] == "empty_query"

    def test_empty_corpus(self) -> None:
        kept, dropped, _stats = filter_by_relevance([], QUERY)
        assert kept == []
        assert dropped == []

    def test_very_low_threshold_keeps_everything(self) -> None:
        """A threshold of 0 still requires *some* query-term overlap."""
        kept, _dropped, _stats = filter_by_relevance(self._pages(), QUERY, threshold=0.0)
        # The off-topic page shares no query term, so it is dropped; the two
        # that share terms survive.
        assert len(kept) == 2

    def test_zero_overlap_page_is_dropped(self) -> None:
        """A page with no query term at all scores exactly 0 and is dropped."""
        kept, dropped, _stats = filter_by_relevance(
            [{"url": "https://a.com/off", "content": OFF_TOPIC}],
            QUERY,
        )
        assert kept == []
        assert len(dropped) == 1
        assert dropped[0]["relevance_score"] == 0.0

    def test_high_threshold_keeps_only_the_best(self) -> None:
        """A bar nothing clears degrades to the single best page, not to zero."""
        kept, _dropped, stats = filter_by_relevance(self._pages(), QUERY, threshold=1e9, top_k=10)
        # Something matched, so the best page survives as evidence.
        assert len(kept) == 1
        assert kept[0]["url"] == "https://a.com/on"
        assert stats["degraded"] == "kept_best_of_none"

    def test_no_query_match_returns_nothing_rather_than_garbage(self) -> None:
        """Zero relevance must not be presented as evidence."""
        kept, dropped, stats = filter_by_relevance(self._pages(), "completely unrelated query about kayaking")
        assert kept == []
        assert len(dropped) == 3
        assert stats["degraded"] == "no_relevant_page"

    def test_weak_match_still_keeps_the_best_page(self) -> None:
        """Something matched but nothing cleared the bar: keep the best one."""
        pages = [{"url": "https://a.com/on", "content": ON_TOPIC}]
        kept, _dropped, stats = filter_by_relevance(pages, QUERY, threshold=1e9)
        assert len(kept) == 1
        assert stats["degraded"] == "kept_best_of_none"

    def test_input_pages_are_not_mutated(self) -> None:
        pages = self._pages()
        original = [dict(p) for p in pages]
        filter_by_relevance(pages, QUERY)
        assert pages == original
