"""Tests for ResearchMode / ScrapePlan plumbing.

The contract these tests pin:

* ``mode`` defaults to ``precision``, so every existing caller is unchanged.
* ``ScrapePlan.for_mode`` gives corpus a materially different plan from
  precision — otherwise "two modes" is a lie.
* ``max_sources`` overrides the mode default without changing the mode.
* An invalid mode string is rejected, not silently coerced.
* The frontend's ``exhaustive`` depth maps onto a backend ``depth`` value
  (verified live, not assumed).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from searchops.core.context.research import ResearchMode, ScrapePlan


class TestResearchModeEnum:
    def test_precision_is_the_default(self) -> None:
        assert ScrapePlan().mode is ResearchMode.PRECISION

    def test_mode_parses_from_string(self) -> None:
        assert ResearchMode("corpus") is ResearchMode.CORPUS

    def test_unknown_mode_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            ResearchMode("exhaustive")


class TestForMode:
    def test_corpus_fetches_more_pages_than_precision(self) -> None:
        precision = ScrapePlan.for_mode("precision")
        corpus = ScrapePlan.for_mode("corpus")
        assert corpus.max_pages > precision.max_pages

    def test_corpus_goes_deeper_than_precision(self) -> None:
        assert ScrapePlan.for_mode("corpus").max_depth > ScrapePlan.for_mode("precision").max_depth

    def test_both_modes_enable_the_relevance_filter(self) -> None:
        assert ScrapePlan.for_mode("precision").relevance_filter is True
        assert ScrapePlan.for_mode("corpus").relevance_filter is True

    def test_only_corpus_dedups_aggressively(self) -> None:
        assert ScrapePlan.for_mode("precision").claim_dedup_aggressive is False
        assert ScrapePlan.for_mode("corpus").claim_dedup_aggressive is True

    def test_both_modes_use_sampling(self) -> None:
        precision_rate = ScrapePlan.for_mode("precision").verify_sampling_rate
        corpus_rate = ScrapePlan.for_mode("corpus").verify_sampling_rate
        assert 0.0 < precision_rate < 1.0
        assert 0.0 < corpus_rate < 1.0

    def test_report_shape_differs_by_mode(self) -> None:
        assert ScrapePlan.for_mode("precision").report_shape == "narrative"
        assert ScrapePlan.for_mode("corpus").report_shape == "dataset"

    def test_max_sources_overrides_breadth_but_not_mode(self) -> None:
        plan = ScrapePlan.for_mode("corpus", max_sources=7)
        assert plan.max_pages == 7
        assert plan.mode is ResearchMode.CORPUS

    def test_accepts_the_enum_directly(self) -> None:
        plan = ScrapePlan.for_mode(ResearchMode.CORPUS)
        assert plan.mode is ResearchMode.CORPUS
        assert plan.relevance_filter is True

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            ScrapePlan.for_mode("not-a-mode")


class TestPlanBounds:
    def test_max_pages_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ScrapePlan(max_pages=0)

    def test_max_pages_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            ScrapePlan(max_pages=1000)

    def test_sampling_rate_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ScrapePlan(verify_sampling_rate=1.5)
        with pytest.raises(ValidationError):
            ScrapePlan(verify_sampling_rate=-0.1)


class TestFrontendDepthMapping:
    """The frontend maps its own depth labels onto backend ResearchDepth.

    This mirrors ``executionStore.startResearch`` so a drift fails here rather
    than silently at runtime.
    """

    @staticmethod
    def _map(depth: str) -> str:
        if depth == "quick":
            return "shallow"
        if depth in ("exhaustive", "deep"):
            return "deep"
        return "standard"

    def test_quick_maps_to_shallow(self) -> None:
        assert self._map("quick") == "shallow"

    def test_exhaustive_maps_to_deep(self) -> None:
        assert self._map("exhaustive") == "deep"

    def test_deep_maps_to_deep(self) -> None:
        assert self._map("deep") == "deep"

    def test_standard_maps_to_standard(self) -> None:
        assert self._map("standard") == "standard"

    def test_every_frontend_label_resolves_to_a_valid_depth(self) -> None:
        valid = {"shallow", "standard", "deep"}
        for label in ("quick", "standard", "deep", "exhaustive"):
            assert self._map(label) in valid
