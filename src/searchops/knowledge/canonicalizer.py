"""
Entity Canonicalization & Alias Resolution Engine.

Resolves entity name variations to unified canonical IDs
using exact slugification and fuzzy Levenshtein ratio comparison.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


def slugify(text: str) -> str:
    """Convert string into clean snake_case slug for canonical indexing."""
    clean = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    clean = re.sub(r"\b(inc|corp|llc|ltd|co)\b", "", clean).strip()
    return re.sub(r"[-\s]+", "_", clean)


class EntityCanonicalizer:
    """Fuzzy alias resolution engine mapping entity variants to canonical IDs."""

    def __init__(self, similarity_threshold: float = 0.85, redis_cache: Any | None = None) -> None:
        self.similarity_threshold = similarity_threshold
        self.redis_cache = redis_cache
        self._canonical_registry: dict[str, str] = {}  # raw_key -> canonical_id
        self._type_buckets: dict[str, list[str]] = {}  # type_slug -> list of raw_keys

    def canonicalize(self, name: str, entity_type: str) -> str:
        """Resolve entity name and entity_type to a canonical ID string."""
        type_slug = slugify(entity_type) or "concept"
        name_slug = slugify(name) or "unknown"
        raw_key = f"{type_slug}:{name_slug}"

        # 1. Instant O(1) exact lookup
        if raw_key in self._canonical_registry:
            return self._canonical_registry[raw_key]

        # 2. Bounded fuzzy match within the specific entity type bucket
        bucket = self._type_buckets.get(type_slug, [])
        # Cap comparison to the most recent 100 entities in the bucket to guarantee bounded execution time
        for existing_key in bucket[-100:]:
            ratio = SequenceMatcher(None, raw_key, existing_key).ratio()
            if ratio >= self.similarity_threshold:
                canonical_id = self._canonical_registry[existing_key]
                self._canonical_registry[raw_key] = canonical_id
                return canonical_id

        # 3. Register new entity canonical ID
        self._canonical_registry[raw_key] = raw_key
        self._type_buckets.setdefault(type_slug, []).append(raw_key)
        return raw_key

