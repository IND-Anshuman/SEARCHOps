"""
URL seen-set: probabilistic de-duplication at the crawl frontier.

A crawler that hashes every URL into a Python set spends ~10 GB at 100M URLs.
A Bloom filter answers the same question in ~120 MB at a 1% false-positive rate
— roughly a 100x space win — and, backed by Redis, survives process restarts,
which an in-process set cannot.

Design notes
------------
* **RedisBloom is not required.** This server has no ``BF.*`` commands
  (verified: ``BF.ADD`` returns ``unknown command``), so instead of a single
  server-side Bloom filter we use per-URL ``SETNX`` keys. That is *exact*, not
  probabilistic: it never reports a new URL as seen. The probabilistic win is
  retained in :func:`optimal_bit_array_size` / :func:`optimal_hash_count`, which
  size a real Bloom filter for deployments that do have RedisBloom.
* **Fails open by default.** A dead cache must not stop the crawl, so an
  unreachable Redis reports URLs as unseen. Pass ``fail_open=False`` for a
  strict frontier that refuses to run without its cache.
* **Normalisation matters.** Without it, ``/page``, ``/page/`` and
  ``/page#x`` are three distinct entries and the filter leaks.

Usage::

    seen = URLSeenSet(get_redis_client())
    if await seen.seen(url):
        return  # already crawled
    ...
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

log = structlog.get_logger(__name__)

DEFAULT_KEY_PREFIX = "searchops:seen"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 1 week


class RedisUnavailable(RuntimeError):
    """Raised when the seen-set cannot reach Redis and fail_open is False."""


def optimal_bit_array_size(expected_urls: int, false_positive_rate: float) -> int:
    """Return the Bloom filter bit count ``m`` for *n* items at rate *p*.

    ``m = -n·ln(p) / (ln 2)^2``

    Raises:
        ValueError: if *expected_urls* is not positive, or *false_positive_rate*
            is not strictly between 0 and 1.
    """
    if expected_urls <= 0:
        raise ValueError("expected_urls must be positive")
    if not 0.0 < false_positive_rate < 1.0:
        raise ValueError("false_positive_rate must be in (0.0, 1.0)")

    return math.ceil(-expected_urls * math.log(false_positive_rate) / (math.log(2) ** 2))


def optimal_hash_count(bit_count: int, expected_urls: int) -> int:
    """Return the Bloom filter hash count ``k``.

    ``k = (m/n)·ln 2``, clamped to at least 1.
    """
    if bit_count <= 0 or expected_urls <= 0:
        raise ValueError("bit_count and expected_urls must be positive")

    return max(1, round((bit_count / expected_urls) * math.log(2)))


def normalize_url(url: str) -> str:
    """Return a canonical form so trivial variants collapse to one entry.

    Drops the fragment and a single trailing slash on the path. Scheme, host,
    and query string are all significant.
    """
    if not url:
        return ""

    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _fingerprint(url: str) -> str:
    """Stable short digest used as the Redis key suffix."""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:32]


@dataclass
class SeenSetStats:
    """Counters exposing how much work the seen-set actually saved."""

    new_count: int = 0
    repeat_count: int = 0
    degraded_calls: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.new_count + self.repeat_count

    @property
    def skip_rate(self) -> float:
        """Fraction of lookups that were repeats (0.0 when idle)."""
        return self.repeat_count / self.total if self.total else 0.0


@dataclass
class URLSeenSet:
    """Redis-backed URL de-duplication set.

    Args:
        redis_client: An async Redis client exposing ``set``/``get``.
        key_prefix: Key namespace, so several crawls can share one Redis.
        ttl_seconds: Entry lifetime. Bounded so the set cannot grow forever.
        fail_open: Treat URLs as unseen when Redis is unreachable.
    """

    redis_client: Any
    key_prefix: str = DEFAULT_KEY_PREFIX
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    fail_open: bool = True
    stats: SeenSetStats = field(default_factory=SeenSetStats)

    def _key(self, url: str) -> str:
        return f"{self.key_prefix}:{_fingerprint(url)}"

    async def seen(self, url: str, *, force: bool = False) -> bool:
        """Return True if *url* was already recorded, else record it.

        The check and the record are one atomic ``SETNX``, so concurrent
        workers cannot both claim the same URL.

        With ``force=True`` the URL is written unconditionally and ``True`` is
        always returned; the pipeline uses this on the success path to persist
        the frontier entry for a URL that was just fetched, even though the
        gate above already saw it as "new".
        """
        key = self._key(url)
        try:
            if force:
                await self.redis_client.set(key, "1", ex=self.ttl_seconds)
                return True
            was_new = await self.redis_client.set(key, "1", nx=True, ex=self.ttl_seconds)
        except Exception as exc:
            self.stats.degraded_calls += 1
            log.warning("url_seen.redis_unavailable", error=str(exc), fail_open=self.fail_open)
            if self.fail_open:
                return False
            raise RedisUnavailable(str(exc)) from exc

        if was_new:
            self.stats.new_count += 1
            return False

        self.stats.repeat_count += 1
        return True

    async def add(self, url: str) -> None:
        """Record *url* without reporting whether it was already present."""
        await self.seen(url)

    async def reset(self) -> None:
        """Forget every URL under this prefix."""
        try:
            cursor = 0
            pattern = f"{self.key_prefix}:*"
            while True:
                cursor, keys = await self.redis_client.scan(cursor=cursor, match=pattern, count=500)
                if keys:
                    # Real Redis returns bytes keys unless decode_responses=True;
                    # normalise so delete() matches regardless of client config.
                    normalised = [k.decode() if isinstance(k, bytes) else k for k in keys]
                    await self.redis_client.delete(*normalised)
                if not cursor:
                    break
        except Exception as exc:  # pragma: no cover - defensive
            self.stats.errors += 1
            log.warning("url_seen.reset_failed", error=str(exc))

        self.stats.new_count = 0
        self.stats.repeat_count = 0
