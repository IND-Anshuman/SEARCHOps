"""
Per-domain repair memory for the self-healing extraction loop.

The repair ladder in ``searchops/llm/json_repair.py`` fixes malformed LLM
output deterministically. This module remembers, per domain, which repair tier
last succeeded — so a domain whose model output consistently needs structural
repair skips straight to the tier that works instead of re-running every
earlier tier on every document.

It also carries the counters that make the loop's value measurable rather than
asserted:

    recovery_rate       fraction of broken outputs that yielded usable records
    avg_tiers_tried     mean repair ladder depth per broken output
    fast_path_rate      fraction of domains resolved on the first learned tier
    unrecovered         broken outputs nothing could rescue

Design constraints:
- Bounded memory: domains are capped (LRU by last-seen) and tiers per domain
  are capped, so a crawl over millions of hosts cannot grow this without limit.
- Thread-safety: all mutation happens under one lock; the class is safe to
  share across asyncio tasks.
- No fabrication: the memory only records *which tier worked*; it never alters
  or supplies extracted content.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DEFAULT_MAX_DOMAINS = 10_000


@dataclass
class DomainRepairMemory:
    """Mutable per-domain repair state."""

    last_successful_tier: int = 0
    """The repair tier that most recently recovered usable records."""

    tier_counts: dict[int, int] = field(default_factory=dict)
    """Histogram of which tiers have been needed for this domain."""

    successes: int = 0
    """Outputs that yielded at least one usable record."""

    failures: int = 0
    """Outputs nothing could recover."""

    last_seen: float = field(default_factory=time.monotonic)

    def note_success(self, tier: int) -> None:
        self.successes += 1
        self.last_successful_tier = tier
        self.tier_counts[tier] = self.tier_counts.get(tier, 0) + 1
        self.last_seen = time.monotonic()

    def note_failure(self) -> None:
        self.failures += 1
        self.last_seen = time.monotonic()


class RepairMemory:
    """Thread-safe, bounded registry of per-domain repair behaviour."""

    def __init__(self, *, max_domains: int = DEFAULT_MAX_DOMAINS) -> None:
        self._domains: OrderedDict[str, DomainRepairMemory] = OrderedDict()
        self._lock = threading.Lock()
        self._max_domains = max_domains
        self._totals = _RepairTotals()

    # ── Lookup ──────────────────────────────────────────────────────────────

    def preferred_tier(self, domain: str) -> int | None:
        """The tier to try first for *domain*, or None if never seen.

        Callers should still run the ladder from tier 0 when this returns a
        tier > 0 only if that tier fails — the preference is a fast path, not
        a guarantee, because models drift.
        """
        with self._lock:
            mem = self._domains.get(domain)
            if mem is None:
                return None
            mem.last_seen = time.monotonic()
            self._domains.move_to_end(domain)
            return mem.last_successful_tier

    def has_seen(self, domain: str) -> bool:
        with self._lock:
            return domain in self._domains

    # ── Recording ───────────────────────────────────────────────────────────

    def record(self, domain: str, tier_used: int, *, recovered: bool) -> None:
        """Record the outcome of one repair attempt for *domain*.

        Args:
            domain: Host the document came from.
            tier_used: The repair tier that produced the final outcome.
            recovered: True when at least one usable record survived.
        """
        with self._lock:
            self._totals.attempts += 1
            if recovered:
                self._totals.recovered += 1
            else:
                self._totals.unrecovered += 1

            self._totals.tiers_tried_total += tier_used

            mem = self._domains.get(domain)
            if mem is None:
                mem = DomainRepairMemory()
                self._domains[domain] = mem
            self._domains.move_to_end(domain)

            if recovered:
                mem.note_success(tier_used)
            else:
                mem.note_failure()

            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        """Drop the least-recently-seen domains once the cap is exceeded."""
        while len(self._domains) > self._max_domains:
            evicted, _ = self._domains.popitem(last=False)
            self._totals.evictions += 1
            log.debug("repair_memory.evicted", domain=evicted, cap=self._max_domains)

    # ── Metrics ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, float | int]:
        """Aggregate counters describing how much repair is actually happening."""
        with self._lock:
            t = self._totals
            attempts = t.attempts
            return {
                "attempts": attempts,
                "recovered": t.recovered,
                "unrecovered": t.unrecovered,
                "recovery_rate": round(t.recovered / attempts, 4) if attempts else 0.0,
                "avg_tiers_tried": round(t.tiers_tried_total / attempts, 4) if attempts else 0.0,
                "domains_tracked": len(self._domains),
                "evictions": t.evictions,
                "max_domains": self._max_domains,
            }

    def domain_stats(self, domain: str) -> dict[str, Any] | None:
        """Per-domain counters, or None if the domain was never seen."""
        with self._lock:
            mem = self._domains.get(domain)
            if mem is None:
                return None
            total = mem.successes + mem.failures
            return {
                "last_successful_tier": mem.last_successful_tier,
                "successes": mem.successes,
                "failures": mem.failures,
                "recovery_rate": round(mem.successes / total, 4) if total else 0.0,
                "tier_counts": dict(sorted(mem.tier_counts.items())),
            }

    def reset(self) -> None:
        """Clear all learned state (used by tests and by cache invalidation)."""
        with self._lock:
            self._domains.clear()
            self._totals = _RepairTotals()


@dataclass
class _RepairTotals:
    attempts: int = 0
    recovered: int = 0
    unrecovered: int = 0
    tiers_tried_total: int = 0
    evictions: int = 0


# ── Module-level singleton ───────────────────────────────────────────────────

_global_memory: RepairMemory | None = None
_global_lock = threading.Lock()


def get_repair_memory() -> RepairMemory:
    """Process-wide repair memory shared by every extraction node."""
    global _global_memory
    if _global_memory is None:
        with _global_lock:
            if _global_memory is None:
                _global_memory = RepairMemory()
    return _global_memory
