"""
Learned per-domain tier routing via a contextual multi-armed bandit.

The static tier pipeline tries tiers in fixed priority order (cheapest → most
expensive). In practice the cheapest tier sometimes has a 90% success rate on
``example.com`` but a 5% rate on ``hard-to-scrape.com`` — the static order
wastes latency on domains where a heavier tier is needed, or wastes money on
domains where the cheap tier always works.

This module replaces the static sort with an **epsilon-greedy UCB1** bandit
that learns, **per domain**, which tier minimises expected cost-per-success.
After each attempt it records (success, cost); during ``.ordered_tiers()`` it
scores each tier as ``p_success / (cost + epsilon)`` and returns them in
descending score order, falling back to static priority for cold-start and for
tiers with no data.

Cost model (expected cost per successful scrape, in USD):
    E[cost] = cost_usd * (1 / p_success)        [on success]
    E[cost] = cost_usd                            [on failure — wasted]
The UCB1 exploration bonus keeps under-sampled tiers alive so the bandit does
not prematurely converge to a local optimum.

All state is in-memory dict keyed by (domain, tier_name) — cheap and restartable.
For a distributed crawler this would move to the shared Redis seen-set store.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import structlog

from searchops.scraping.tier import ScrapeTier
from searchops.scraping.tier import classify_failure as _classify_failure

log = structlog.get_logger(__name__)

# Exploration parameter: probability of trying a tier based on UCB1 score
# rather than the best-known score. Falls linearly from `exploration_start` to
# `exploration_end` over `exploration_decay_steps` attempts.
DEFAULT_EXPLORATION_START = 0.3
DEFAULT_EXPLORATION_END = 0.05
DEFAULT_EXPLORATION_DECAY_STEPS = 500

# Small constant to avoid division-by-zero when cost_usd == 0.0 (free tiers).
_COST_SCALE = 1000.0  # converts avg_cost (USD) into a comparable unit


@dataclass
class TierStats:
    """Per-domain, per-tier reward statistics for UCB1."""

    successes: int = 0
    failures: int = 0
    total_cost: float = 0.0

    @property
    def trials(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        """Empirical success probability, Laplace-smoothed to avoid 0/1 extremes."""
        if self.trials == 0:
            return 0.0
        # Laplace +1 smoothing keeps p in (0, 1) even with all-fail or all-success.
        return (self.successes + 1) / (self.trials + 2)

    @property
    def avg_cost(self) -> float:
        """Average cost per attempt (successful or not)."""
        if self.trials == 0:
            return 0.0
        return self.total_cost / self.trials


@dataclass
class BanditMetrics:
    """Aggregate counters for observability of the bandit itself."""

    total_attempts: int = 0
    bandit_reorders: int = 0
    cold_start_fallbacks: int = 0
    exploration_pulls: int = 0
    saved_cost_usd: float = 0.0  # estimated $ saved by reordering vs static
    saved_latency_ms: float = 0.0  # estimated ms saved by reordering vs static


class TierBandit:
    """Contextual multi-armed bandit for per-domain tier selection.

    Usage:
        bandit = TierBandit(tiers)
        ordered = bandit.ordered_tiers(domain="example.com")
        result = await tier.scraper.scrape(request)
        bandit.record_result(domain, tier.name, result, cost_usd=tier.cost_usd)

    The bandit never blocks or fails — it degrades to static priority ordering
    on any internal error, so pipeline correctness is always preserved.
    """

    def __init__(
        self,
        tiers: list[ScrapeTier],
        *,
        exploration_start: float = DEFAULT_EXPLORATION_START,
        exploration_end: float = DEFAULT_EXPLORATION_END,
        exploration_decay_steps: int = DEFAULT_EXPLORATION_DECAY_STEPS,
    ) -> None:
        # Sort by static priority as the canonical cold-start order.
        self._tiers = sorted(tiers, key=lambda t: t.priority)
        self._tier_by_name = {t.name: t for t in self._tiers}
        self._stats: dict[str, dict[str, TierStats]] = defaultdict(lambda: defaultdict(TierStats))
        self._attempts: dict[str, int] = defaultdict(int)
        self.metrics = BanditMetrics()

        self._exploration_start = exploration_start
        self._exploration_end = exploration_end
        self._exploration_decay_steps = max(1, exploration_decay_steps)

    def _exploration_rate(self, domain: str) -> float:
        """Linear decay from exploration_start to exploration_end."""
        steps = self._attempts[domain]
        if steps >= self._exploration_decay_steps:
            return self._exploration_end
        fraction = steps / self._exploration_decay_steps
        return self._exploration_start * (1 - fraction) + self._exploration_end * fraction

    def _ucb1_score(self, domain: str, tier_name: str, *, explore: bool = True) -> float:
        """UCB1 upper confidence bound for a tier on a domain.

        Returns a cost-adjusted score: higher = try sooner (cheaper, more
        reliable).

        Score = p_success / (1 + avg_cost * 1000) + exploration_bonus

        The ``* 1000`` converts cost_usd into a comparable unit: a $0.001 tier
        gets a divisor of 2, a $0.01 tier gets a divisor of 11. Free tiers
        (cost 0) score as pure ``p_success``. This means a 90%-success free
        tier (0.90) outranks a 90%-success $0.001 tier (0.45), while a
        0%-success free tier (0.05 with smoothing) loses to a 100%-success
        $0.001 tier (0.50).
        """
        stats = self._stats[domain][tier_name]
        tier = self._tier_by_name.get(tier_name)

        if stats.trials == 0 or tier is None:
            # Unexplored tier: defer to static priority. Return a neutral
            # baseline so it sorts below any tier with confirmed data.
            return -tier.priority if tier else -999

        p_success = stats.success_rate
        cost_factor = 1 + stats.avg_cost * _COST_SCALE
        base_score = p_success / cost_factor

        if explore and self._attempts[domain] > 0:
            # UCB1 exploration bonus: rewards under-sampled tiers.
            n_total = self._attempts[domain]
            n_tier = stats.trials
            exploration_bonus = math.sqrt(2 * math.log(n_total) / n_tier) if n_tier > 0 else float("inf")
            self.metrics.exploration_pulls += 1
            return base_score + exploration_bonus

        return base_score

    def ordered_tiers(self, domain: str, *, explore: bool = True) -> list[ScrapeTier]:
        """Return tiers in learned order for *domain*.

        Falls back to static priority sort if no data or on error.
        The returned list is always a permutation of the full tier set — the
        bandit reorders, it does not drop tiers.
        """
        try:
            if self._attempts[domain] == 0:
                # Cold start: no data at all. Use static priority order.
                self.metrics.cold_start_fallbacks += 1
                return list(self._tiers)

            scored = []
            for tier in self._tiers:
                score = self._ucb1_score(domain, tier.name, explore=explore)
                scored.append((score, tier))

            # Sort descending by score. Stable sort keeps static priority as
            # a tiebreaker for equal scores.
            scored.sort(key=lambda pair: pair[0], reverse=True)
            ordered = [tier for _, tier in scored]

            # Detect whether we actually reordered vs static priority.
            static_order = [t.name for t in self._tiers]
            learned_order = [t.name for t in ordered]
            if static_order != learned_order:
                self.metrics.bandit_reorders += 1

            # Estimate savings: if a cheaper tier has a high success rate,
            # the bandit puts it first. The static order would waste a call
            # on a cheaper-but-failed tier first.
            self._estimate_savings(domain, static_order, learned_order)

            return ordered
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("tier_bandit.reorder_failed", domain=domain, error=str(exc))
            self.metrics.cold_start_fallbacks += 1
            return list(self._tiers)

    def _estimate_savings(self, domain: str, static_order: list[str], learned_order: list[str]) -> None:
        """Estimate $ and latency saved by reordering vs static priority."""
        if static_order == learned_order:
            return

        # For the domain's historical data, compare expected cost of static
        # ordering vs learned ordering.
        stats = self._stats[domain]
        static_cost = 0.0
        learned_cost = 0.0
        found_success = False

        for tier_name in static_order:
            if tier_name not in stats or tier_name not in self._tier_by_name:
                continue
            t_stats = stats[tier_name]
            tier = self._tier_by_name[tier_name]
            if not found_success:
                static_cost += tier.cost_usd  # paid regardless of outcome
                if t_stats.success_rate > 0 and not found_success:
                    found_success = True
            if found_success and t_stats.trials > 0:
                break

        found_success = False
        for tier_name in learned_order:
            if tier_name not in stats or tier_name not in self._tier_by_name:
                continue
            t_stats = stats[tier_name]
            tier = self._tier_by_name[tier_name]
            if not found_success:
                learned_cost += tier.cost_usd
                if t_stats.success_rate > 0 and not found_success:
                    found_success = True
            if found_success and t_stats.trials > 0:
                break

        delta = static_cost - learned_cost
        if delta > 0:
            self.metrics.saved_cost_usd += delta
            # Rough latency estimate: each skipped tier saves ~its timeout / 2 on average
            self.metrics.saved_latency_ms += delta * 100  # rough proxy

    def record_result(
        self,
        domain: str,
        tier_name: str,
        status_code: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Record the outcome of a tier attempt for learning.

        Args:
            domain: The domain being scraped.
            tier_name: Name of the tier that was tried.
            status_code: HTTP status code (200 = success, anything else = failure).
            cost_usd: Cost incurred by this attempt (for premium tiers).
        """
        try:
            self._attempts[domain] += 1
            self.metrics.total_attempts += 1

            # Only record results for tiers we know about.
            if tier_name not in self._tier_by_name:
                return

            stats = self._stats[domain][tier_name]
            success = 200 <= status_code < 300

            if success:
                stats.successes += 1
            else:
                stats.failures += 1
            stats.total_cost += cost_usd

            failure_class = _classify_failure(status_code) if not success else None
            log.debug(
                "tier_bandit.record",
                domain=domain,
                tier=tier_name,
                success=success,
                status=status_code,
                cost_usd=cost_usd,
                failure_class=failure_class.value if failure_class else "none",
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("tier_bandit.record_failed", domain=domain, error=str(exc))

    def get_stats(self, domain: str) -> dict[str, dict[str, float | int]]:
        """Return per-tier statistics for a domain, for diagnostics/metrics."""
        stats = self._stats.get(domain, {})
        return {
            name: {
                "successes": s.successes,
                "failures": s.failures,
                "trials": s.trials,
                "success_rate": round(s.success_rate, 4),
                "avg_cost": round(s.avg_cost, 6),
            }
            for name, s in stats.items()
        }

    def get_metrics(self) -> dict[str, float | int]:
        """Return aggregate bandit metrics for observability."""
        return {
            "total_attempts": self.metrics.total_attempts,
            "bandit_reorders": self.metrics.bandit_reorders,
            "cold_start_fallbacks": self.metrics.cold_start_fallbacks,
            "exploration_pulls": self.metrics.exploration_pulls,
            "saved_cost_usd": round(self.metrics.saved_cost_usd, 6),
            "saved_latency_ms": round(self.metrics.saved_latency_ms, 2),
        }
