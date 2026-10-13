"""Deterministic BM25 relevance filtering — the free volume cut.

Why this stage exists
---------------------
Corpus mode fetches many pages and most are off-topic for the query. Before
this stage, *every* fetched page went to the LLM, so the crawl's volume was
paid for in tokens. BM25 scores each page against the query for free, so
off-topic pages are dropped before any LLM call sees them.

This is the single most important cost lever in the funnel: the cheapest LLM
call is the one that never happens.

Design decisions
----------------
* **No embeddings, no network, no model.** Pure term statistics. Deterministic
  and auditable — the same corpus and query always produce the same ranking,
  which is what makes the funnel explainable.
* **Stopwords removed** so "what is a vector database" ranks on *vector* and
  *database*, not on *what* and *is*.
* **Threshold and top-K both apply.** A page below the threshold is dropped
  even inside the top K; the top-K caps survivors regardless.
* **Degrades instead of denying.** An empty/stopword-only query returns the
  corpus unchanged rather than silently discarding it, and if the threshold
  would drop everything the single best page is kept so the run still has
  evidence.
* **Inputs are never mutated**; kept pages get their verdict attached to a
  copy, so the caller's state is untouched.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")

# A compact stopword list: high-frequency English words that carry no topical
# signal. Deliberately small — over-pruning hurts recall more than it helps
# precision.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "it's",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "can",
        "will",
        "just",
        "should",
        "now",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
    ]
)

# BM25 parameters (Robertson/Sparck-Jones defaults).
_K1 = 1.5
_B = 0.75


class RelevanceVerdict(StrEnum):
    """Why a page was kept or dropped."""

    KEPT = "kept"
    DROPPED = "dropped"


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS]


def _stem(token: str) -> str:
    """Light suffix stripping so 'databases' and 'database' share a form.

    Only unambiguous endings are removed. ``-es`` is stripped only when the
    remaining base ends in s/x/z/ch/sh ('boxes' -> 'box'); otherwise the bare
    ``-s`` is dropped ('databases' -> 'database'). Doubled consonants from
    '-ing'/'-ed' are folded back ('running' -> 'runn' -> 'run').
    """
    for suffix in ("ies", "ied", "ing", "ed", "s", "'s"):
        if not token.endswith(suffix) or len(token) - len(suffix) < 3:
            continue
        base = token[: -len(suffix)]

        if suffix in ("ies", "ied"):
            return base + "y"

        if suffix == "s":
            # 'boxes'/'dishes' -> the trailing '-es' is the plural marker.
            if base.endswith(("s", "x", "z", "ch", "sh")) and token.endswith("es"):
                return base
            # 'databases' -> 'database'; 'stops' -> 'stop'
            return base

        # 'stopped' -> 'stopp' -> 'stop'
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in "aeiou":
            base = base[:-1]
        return base
    return token


@dataclass
class BM25Scorer:
    """Okapi BM25 scorer for one query against many documents.

    IDF is computed over the *supplied corpus*, so the score is relative to
    that set — which is exactly what a per-run relevance filter wants (a term
    rare in this corpus is informative for this run).
    """

    query: str
    k1: float = _K1
    b: float = _B
    _query_terms: list[str] = field(init=False)
    _idf: dict[str, float] = field(init=False, default_factory=dict)
    _avg_len: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self._query_terms = [_stem(t) for t in _tokens(self.query)]

    def score(self, document: str) -> float:
        """BM25 score of *document* against the query.

        Uses corpus-independent IDF weighting when the document set is not
        known (single-document scoring); see :meth:`fit` for corpus-relative
        scoring, which :func:`filter_by_relevance` uses.
        """
        if not self._query_terms or not document:
            return 0.0
        terms = [_stem(t) for t in _tokens(document)]
        if not terms:
            return 0.0

        counts: dict[str, int] = {}
        for t in terms:
            counts[t] = counts.get(t, 0) + 1

        total = 0.0
        for qt in self._query_terms:
            tf = counts.get(qt, 0)
            if tf == 0:
                continue
            # Without a corpus, weight every query term equally; the TF
            # saturation still applies so keyword stuffing cannot dominate.
            idf = self._idf.get(qt, 1.0)
            total += idf * (tf * (self.k1 + 1)) / (tf + self.k1)
        return total

    def fit(self, documents: list[str]) -> None:
        """Compute IDF over *documents* so scores are corpus-relative."""
        if not documents:
            self._idf = {}
            self._avg_len = 0.0
            return

        n_docs = len(documents)
        doc_freq: dict[str, int] = {}
        lengths: list[int] = []

        for doc in documents:
            terms = [_stem(t) for t in _tokens(doc)]
            lengths.append(len(terms))
            for term in set(terms):
                doc_freq[term] = doc_freq.get(term, 0) + 1

        self._avg_len = sum(lengths) / n_docs if n_docs else 0.0
        # Robertson IDF: ln(1 + (N - df + 0.5) / (df + 0.5)), always positive.
        self._idf = {term: math.log(1 + (n_docs - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()}

    def score_corpus_relative(self, document: str) -> float:
        """BM25 score using the fitted corpus statistics (IDF + length norm)."""
        if not self._query_terms or not document or not self._idf:
            return 0.0
        terms = [_stem(t) for t in _tokens(document)]
        if not terms:
            return 0.0

        counts: dict[str, int] = {}
        for t in terms:
            counts[t] = counts.get(t, 0) + 1

        doc_len = len(terms)
        avg = self._avg_len or doc_len
        total = 0.0
        for qt in self._query_terms:
            tf = counts.get(qt, 0)
            if tf == 0:
                continue
            idf = self._idf.get(qt, 0.0)
            norm = tf + self.k1 * (1 - self.b + self.b * (doc_len / avg))
            total += idf * (tf * (self.k1 + 1)) / norm
        return total


def filter_by_relevance(
    pages: list[dict[str, Any]],
    query: str,
    *,
    threshold: float = 0.0,
    top_k: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Split *pages* into (kept, dropped, stats) by BM25 relevance.

    Args:
        pages: Dicts with at least ``url`` and ``content``.
        query: The research query. A stopword-only query is treated as
            "no filter" and the corpus is returned unchanged.
        threshold: Minimum corpus-relative BM25 score to survive. BM25 is not
            normalised to 0-1, so the default of ``0.0`` means "matched at
            least one query term" — a page with zero query-term overlap scores
            exactly 0 and is dropped. Raise it to demand stronger matches.
        top_k: Hard cap on survivors, highest-scoring first.

    Returns:
        ``(kept, dropped, stats)``. Inputs are never mutated; kept and dropped
        pages are copies carrying ``relevance`` and ``relevance_score``.
    """
    stats: dict[str, Any] = {
        "pages_in": len(pages),
        "pages_kept": 0,
        "pages_dropped": 0,
        "degraded": "",
    }
    if not pages:
        return [], [], stats

    query_terms = _tokens(query)
    if not query_terms:
        # Degrade, do not deny: with no signal, filtering would just throw the
        # whole corpus away.
        stats["degraded"] = "empty_query"
        kept = [dict(p, relevance=RelevanceVerdict.KEPT.value, relevance_score=0.0) for p in pages]
        stats["pages_kept"] = len(kept)
        log.info("relevance.degraded", reason="empty_query", pages=len(pages))
        return kept, [], stats

    scorer = BM25Scorer(query)
    contents = [str(p.get("content") or "") for p in pages]
    scorer.fit(contents)
    scored = [(scorer.score_corpus_relative(c), p) for c, p in zip(contents, pages, strict=True)]

    survivors = [(s, p) for s, p in scored if s > threshold]
    if not survivors and scored and max(s for s, _p in scored) > 0:
        # Degradation, not denial: when *something* matched but nothing cleared
        # the threshold, keep the single best page so the run still has
        # evidence. When nothing matched at all, keeping a zero-relevance page
        # would just feed garbage to the LLM — return an empty corpus and let
        # the caller report it honestly.
        survivors = [max(scored, key=lambda pair: pair[0])]
        stats["degraded"] = "kept_best_of_none"
        log.warning("relevance.degraded", reason="no_page_passed_threshold")
    elif not survivors:
        stats["degraded"] = "no_relevant_page"
        log.warning("relevance.degraded", reason="no_query_term_matched")

    survivors.sort(key=lambda pair: pair[0], reverse=True)
    kept_pairs = survivors[:top_k]
    kept_urls = {id(p) for _s, p in kept_pairs}

    kept = [dict(p, relevance=RelevanceVerdict.KEPT.value, relevance_score=round(s, 4)) for s, p in kept_pairs]
    dropped = [
        dict(p, relevance=RelevanceVerdict.DROPPED.value, relevance_score=round(s, 4))
        for s, p in scored
        if id(p) not in kept_urls
    ]

    stats["pages_kept"] = len(kept)
    stats["pages_dropped"] = len(dropped)
    log.info(
        "relevance.filtered",
        query=query[:60],
        kept=len(kept),
        dropped=len(dropped),
        threshold=threshold,
    )
    return kept, dropped, stats
