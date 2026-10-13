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

import contextlib
import re
from typing import Any
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy imports
# ---------------------------------------------------------------------------

try:
    from datasketch import MinHash, MinHashLSH
except ImportError:  # pragma: no cover
    MinHash = None
    MinHashLSH = None


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
        self.num_perm = num_perm
        self.shingle_n = shingle_n
        self._lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
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
            with contextlib.suppress(Exception):
                self._lsh.remove(doc_id)

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
                doc_id
                for doc_id in matches
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


def build_deduplicator_from_config(
    *,
    threshold: float = 0.85,
    storage_config: dict[str, Any] | None = None,
    redis_url: str | None = None,
    prefer_persistent: bool = True,
) -> MinHashDeduplicator:
    """Build the best deduplicator the current environment supports.

    Prefers :class:`PersistentMinHashDeduplicator` so the index survives a
    process restart — a long crawl otherwise re-fetches and re-pays for content
    it already processed. Falls back to the in-memory deduplicator when no Redis
    is configured or the persistent backend cannot be constructed, so dedup is
    never a hard dependency on infrastructure.

    Args:
        threshold: Jaccard similarity threshold for near-duplicate detection.
        storage_config: Explicit datasketch storage config, if already built.
        redis_url: Redis connection string used to derive ``storage_config``.
        prefer_persistent: Set False to force the in-memory implementation.

    Returns:
        A working deduplicator. Never raises for a missing/invalid Redis —
        the caller gets the in-memory variant instead.
    """
    if not prefer_persistent:
        return MinHashDeduplicator(threshold=threshold)

    config = storage_config
    if config is None and redis_url:
        config = _storage_config_from_url(redis_url)

    if config is not None:
        try:
            return PersistentMinHashDeduplicator(threshold=threshold, storage_config=config)
        except Exception as exc:
            log.warning(
                "deduplicator.persistent_unavailable",
                error=str(exc),
                fallback="in_memory",
            )

    return MinHashDeduplicator(threshold=threshold)


def _storage_config_from_url(redis_url: str) -> dict[str, Any]:
    """Translate a Redis URL into a datasketch storage config.

    Accepts ``redis://`` and ``rediss://`` (TLS). Returns an empty dict for an
    unrecognised scheme so the caller falls back to in-memory rather than
    constructing a client that cannot connect.
    """
    parsed = urlparse(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        return {}

    config: dict[str, Any] = {
        "type": "redis",
        "redis": {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 6379,
        },
    }
    if parsed.path and parsed.path != "/":
        # Redis URLs encode the DB index in the path: redis://host:6379/2
        with contextlib.suppress(ValueError):
            config["redis"]["db"] = int(parsed.path.lstrip("/"))
    if parsed.password:
        config["redis"]["password"] = parsed.password
    if parsed.scheme == "rediss":
        config["redis"]["ssl"] = True
    return config


class PersistentMinHashDeduplicator(MinHashDeduplicator):
    """MinHash+LSH deduplicator backed by Redis storage.

    The in-memory :class:`MinHashDeduplicator` keeps its whole index in the
    process, so a restart forgets every document ever seen — a long crawl then
    re-fetches and re-pays for content it has already processed. This variant
    hands datasketch a ``storage_config`` so the LSH bands live in Redis and
    survive restarts.

    The detection semantics are identical to the in-memory class; only the
    storage backend differs.
    """

    def __init__(
        self,
        threshold: float = 0.85,
        num_perm: int = 128,
        shingle_n: int = 3,
        *,
        storage_config: dict[str, Any] | None = None,
        storage: Any = None,
    ) -> None:
        """Build a Redis-backed deduplicator.

        Args:
            storage_config: datasketch storage config. Must contain a "redis"
                block — datasketch requires it even when ``storage`` is given.
            storage: Optional pre-built storage backend (any object satisfying
                datasketch's storage protocol). Supplying one avoids opening a
                live Redis connection at construction time, which is what the
                tests do.
        """
        if MinHash is None or MinHashLSH is None:  # pragma: no cover
            raise RuntimeError("datasketch is not installed. Run: uv add 'datasketch>=1.6.0'")

        # datasketch requires a "redis" block in the config even when the caller
        # supplies its own backend object, so always provide one.
        base: dict[str, Any] = {"type": "redis", "redis": {"host": "localhost", "port": 6379}}
        if storage_config:
            base.update(storage_config)
        base.setdefault("type", "redis")
        base.setdefault("redis", {"host": "localhost", "port": 6379})
        storage_config = base

        self.threshold = threshold
        self.num_perm = num_perm
        self.shingle_n = shingle_n
        self.storage_config = storage_config
        self._lsh = MinHashLSH(
            threshold=threshold,
            num_perm=num_perm,
            storage_config=storage_config,
        )
        # Swap in the injected backend so no live connection is opened here.
        if storage is not None:
            self._lsh._storage = storage
        self._doc_map: dict[str, MinHash] = {}


def build_persistent_deduplicator(
    *,
    threshold: float = 0.85,
    storage_config: dict[str, Any] | None = None,
    storage: Any = None,
) -> PersistentMinHashDeduplicator:
    """Build a Redis-backed deduplicator whose index survives restarts."""
    return PersistentMinHashDeduplicator(threshold=threshold, storage_config=storage_config, storage=storage)
