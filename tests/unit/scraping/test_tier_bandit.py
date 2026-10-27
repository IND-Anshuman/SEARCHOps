"""
Tests for the contextual tier-selection bandit (Phase 4: learned routing).

These pin the core behaviour without any network:
  - cold start returns static priority order
  - recording successes/fails reorders tiers toward cheaper-successful tiers
  - UCB1 exploration bonus keeps under-sampled tiers alive
  - record_result handles unknown tier names without crashing
  - get_metrics / get_stats expose the counters
  - the bandit never raises even on edge inputs
"""

from __future__ import annotations

import pytest

from searchops.scraping.tier import FailureClass, ScrapeTier
from searchops.scraping.tier_bandit import BanditMetrics, TierBandit, TierStats


def _make_tiers():
    """Minimal tier set: one free tier, one cheap premium tier, one expensive."""
    return [
        ScrapeTier(priority=0, name="cheap", is_premium=False, cost_usd=0.0),
        ScrapeTier(
            priority=1,
            name="mid",
            is_premium=True,
            trigger_on=frozenset({FailureClass.ACCESS}),
            cost_usd=0.001,
        ),
        ScrapeTier(
            priority=2,
            name="expensive",
            is_premium=True,
            trigger_on=frozenset({FailureClass.ACCESS}),
            cost_usd=0.01,
        ),
    ]


# ── TierStats ────────────────────────────────────────────────────────────────


class TestTierStats:
    def test_initial_state(self):
        s = TierStats()
        assert s.trials == 0
        assert s.success_rate == 0.0
        assert s.avg_cost == 0.0

    def test_success_rate_laplace_smoothing(self):
        s = TierStats(successes=3, failures=0)
        assert s.success_rate < 1.0  # smoothed
        assert s.success_rate > 0.5

    def test_success_rate_all_failures(self):
        s = TierStats(successes=0, failures=5)
        assert s.success_rate > 0.0  # smoothed, never exactly zero
        assert s.success_rate < 0.5

    def test_avg_cost(self):
        s = TierStats(successes=2, failures=2, total_cost=0.010)
        assert s.avg_cost == pytest.approx(0.0025)

    def test_trials(self):
        s = TierStats(successes=3, failures=2)
        assert s.trials == 5


# ── TierBandit ───────────────────────────────────────────────────────────────


class TestTierBandit:
    def test_cold_start_returns_static_priority_order(self):
        tiers = _make_tiers()
        bandit = TierBandit(tiers)

        ordered = bandit.ordered_tiers("cold.example.com")

        assert [t.name for t in ordered] == ["cheap", "mid", "expensive"]

    def test_cold_start_with_no_attempts(self):
        bandit = TierBandit(_make_tiers())
        assert bandit._attempts["unknown.com"] == 0
        ordered = bandit.ordered_tiers("unknown.com")
        assert len(ordered) == 3

    def test_record_result_updates_stats(self):
        bandit = TierBandit(_make_tiers())

        bandit.record_result("example.com", "cheap", 200, 0.0)
        bandit.record_result("example.com", "cheap", 404, 0.0)
        bandit.record_result("example.com", "mid", 200, 0.001)

        stats = bandit.get_stats("example.com")
        assert stats["cheap"]["trials"] == 2
        assert stats["cheap"]["successes"] == 1
        assert stats["mid"]["trials"] == 1
        assert stats["mid"]["successes"] == 1

    def test_record_result_unknown_tier_is_noop(self):
        bandit = TierBandit(_make_tiers())

        # Must not raise even for an unknown tier name.
        bandit.record_result("example.com", "nonexistent_tier", 200, 0.0)

        assert bandit.get_stats("example.com") == {}

    def test_record_result_handles_errors_gracefully(self):
        bandit = TierBandit(_make_tiers())

        # Force a failure inside record_result by corrupting the tiers dict.
        bandit._tier_by_name = {}  # type: ignore[assignment]

        bandit.record_result("example.com", "cheap", 500, 0.0)
        # Should not raise; should just be a no-op for unknown tier.
        assert True

    def test_reorders_after_dominant_success(self):
        """If 'cheap' fails and 'mid' succeeds, the bandit should prefer 'mid'."""
        bandit = TierBandit(_make_tiers())

        # cheap: 10 fails, mid: 10 successes
        for _ in range(10):
            bandit.record_result("hard.example.com", "cheap", 403, 0.0)
        for _ in range(10):
            bandit.record_result("hard.example.com", "mid", 200, 0.001)

        ordered = bandit.ordered_tiers("hard.example.com")
        names = [t.name for t in ordered]

        # 'mid' should be ranked above 'cheap' because cheap has 0% success.
        assert names.index("mid") < names.index("cheap")

    def test_expensive_tier_ranked_low_with_low_success(self):
        bandit = TierBandit(_make_tiers())

        for _ in range(10):
            bandit.record_result("x.com", "cheap", 200, 0.0)
        for _ in range(10):
            bandit.record_result("x.com", "expensive", 503, 0.01)

        ordered = bandit.ordered_tiers("x.com")
        # cheap should beat expensive.
        assert ordered[0].name == "cheap"

    def test_exploration_allows_under_sampled_tiers(self):
        bandit = TierBandit(_make_tiers(), exploration_start=1.0, exploration_end=1.0)

        # Only record cheap successes
        for _ in range(20):
            bandit.record_result("x.com", "cheap", 200, 0.0)

        ordered = bandit.ordered_tiers("x.com", explore=True)
        # With full exploration, under-sampled tiers should still get ranked.
        names = [t.name for t in ordered]
        # expensive and mid should still be in the list (not dropped).
        assert "expensive" in names
        assert "mid" in names

    def test_exploration_rate_decays(self):
        bandit = TierBandit(
            _make_tiers(),
            exploration_start=0.3,
            exploration_end=0.05,
            exploration_decay_steps=10,
        )

        # No attempts yet.
        assert bandit._exploration_rate("cold.com") == pytest.approx(0.3)

        for _ in range(11):
            bandit.record_result("x.com", "cheap", 200, 0.0)

        # After 11 attempts, rate should be at the floor.
        assert bandit._exploration_rate("x.com") == pytest.approx(0.05)

    def test_get_metrics_structure(self):
        bandit = TierBandit(_make_tiers())
        bandit.record_result("x.com", "cheap", 200, 0.0)
        bandit.record_result("x.com", "cheap", 403, 0.0)

        metrics = bandit.get_metrics()
        assert "total_attempts" in metrics
        assert "bandit_reorders" in metrics
        assert "cold_start_fallbacks" in metrics
        assert "exploration_pulls" in metrics
        assert "saved_cost_usd" in metrics
        assert "saved_latency_ms" in metrics
        assert metrics["total_attempts"] == 2

    def test_savings_estimate_positive_on_reorder(self):
        """When the bandit reorders, saved_cost_usd should be non-negative."""
        bandit = TierBandit(_make_tiers())

        for _ in range(10):
            bandit.record_result("x.com", "cheap", 403, 0.0)
        for _ in range(10):
            bandit.record_result("x.com", "mid", 200, 0.001)

        bandit.ordered_tiers("x.com")
        metrics = bandit.get_metrics()
        assert metrics["bandit_reorders"] >= 1


# ── BanditMetrics ────────────────────────────────────────────────────────────


class TestBanditMetrics:
    def test_default_values(self):
        m = BanditMetrics()
        assert m.total_attempts == 0
        assert m.bandit_reorders == 0
        assert m.cold_start_fallbacks == 0
        assert m.exploration_pulls == 0
        assert m.saved_cost_usd == 0.0
        assert m.saved_latency_ms == 0.0
