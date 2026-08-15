"""
Near-Deduplication & Document Fingerprinting Engine (Phase 6).

Uses datasketch MinHash (128 permutation functions) and Locality Sensitive Hashing (LSH)
to detect duplicate or near-duplicate web documents and PDFs across the scraping pipeline.

Features:
- N-gram word shingling (default N=3).
- MinHash signature generation (128 hash permutations).
- Sub-linear time LSH similarity search with configurable Jaccard threshold (default 0.85).
- In-memory index with option for persistent key-value indexing.

Usage::

    from searchops.scraping.deduplicator import MinHashDeduplicator

    dedup = MinHashDeduplicator(threshold=0.85)

    # Index document
    dedup.index_document("doc1", "This is the full text content of page one.")

    # Check if a new document is a near-duplicate
    is_dup, matched_ids = dedup.is_duplicate("This is the full text content of page one with a small change.")
    if is_dup:
        print(f"Duplicate of: {matched_ids}")
"""

from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy imports
# ---------------------------------------------------------------------------

try:
    from datasketch import MinHash, MinHashLSH  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    MinHash = None     # type: ignore[assignment,misc]
    MinHashLSH = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# MinHashDeduplicator
# ---------------------------------------------------------------------------

class MinHashDeduplicator:
    """
    MinHash + MinHashLSH Near-Deduplication Engine.

    Parameters:
        threshold: Jaccard similarity threshold for considering two documents near-duplicates (0.0 to 1.0).
        num_perm:   Number of permutation functions for MinHash signature (default 128).
        shingle_n:  Word N-gram size for shingle extraction (default 3).
    """

    def __init__(
        self,
        threshold: float = 0.85,
        num_perm: int = 128,
        shingle_n: int = 3,
    ) -> None:
        if MinHash is None or MinHashLSH is None:  # pragma: no cover
            raise RuntimeError("datasketch is not installed. Run: uv add 'datasketch>=1.6.0'")

        self.threshold = threshold
        self.num_perm  = num_perm
        self.shingle_n = shingle_n
        self._lsh      = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self._doc_map: dict[str, MinHash] = {}


    def compute_minhash(self, text: str) -> Any:
        """
        Compute MinHash signature for a text string.

        Converts text into normalized word N-gram shingles and updates MinHash.
        """
        m = MinHash(num_perm=self.num_perm)
        shingles = self.create_shingles(text, n=self.shingle_n)
        for s in shingles:
            m.update(s.encode("utf-8"))
        return m

    def create_shingles(self, text: str, n: int = 3) -> list[str]:
        """
        Generate N-gram word shingles from input text.

        Cleans punctuation, lowercases, and creates overlapping N-word windows.
        """
        clean_text = re.sub(r"[^\w\s]", "", text.lower())
        words = clean_text.split()
        if len(words) < n:
            return [" ".join(words)] if words else []

        return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]

    def index_document(self, doc_id: str, text: str) -> None:
        """
        Compute MinHash for *text* and index it under *doc_id* in the LSH index.
        """
        m = self.compute_minhash(text)
        if doc_id in self._doc_map:
            try:
                self._lsh.remove(doc_id)
            except Exception:
                pass

        self._lsh.insert(doc_id, m)
        self._doc_map[doc_id] = m
        log.debug("deduplicator.indexed", doc_id=doc_id, shingle_count=len(self.create_shingles(text)))

    def is_duplicate(self, text: str, threshold: float | None = None) -> tuple[bool, list[str]]:
        """
        Check if *text* is a near-duplicate of any previously indexed document.

        Returns:
            Tuple of (is_duplicate: bool, matching_doc_ids: list[str]).
        """
        m = self.compute_minhash(text)
        matches = self._lsh.query(m)

        if not matches:
            return False, []

        # If a custom threshold is specified, verify exact Jaccard score
        if threshold is not None and threshold != self.threshold:
            filtered = [
                doc_id for doc_id in matches
                if doc_id in self._doc_map and m.jaccard(self._doc_map[doc_id]) >= threshold
            ]
            return len(filtered) > 0, filtered

        return True, matches

    def jaccard_similarity(self, text1: str, text2: str) -> float:
        """Compute estimated Jaccard similarity between two text strings."""
        m1 = self.compute_minhash(text1)
        m2 = self.compute_minhash(text2)
        return float(m1.jaccard(m2))

    def clear(self) -> None:
        """Clear all indexed documents from the LSH index."""
        self._lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        self._doc_map.clear()



def build_deduplicator(*, threshold: float = 0.85) -> MinHashDeduplicator:
    """Factory helper to build a MinHashDeduplicator."""
    return MinHashDeduplicator(threshold=threshold)

