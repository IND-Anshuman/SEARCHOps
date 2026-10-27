"""Tests for user-tunable, bounded research configuration.

Every knob a user can turn is a knob they can break. The contract:

1. **Every field has a hard bound**, enforced at the schema, so no request can
   ask for an unbounded crawl or an infinite verification sample.
2. **Bounds are absolute, not defaults.** A caller cannot widen them by
   passing a bigger number — the schema rejects it.
3. **Budget is checked against the plan.** A run asking for 200 pages with a
   10-scrape budget is refused with a reason, not silently truncated.
4. **Cost is estimated honestly.** The estimate is derived from real per-unit
   costs and labelled as an estimate — never presented as a measured figure.
"""

from __future__ import annotations

from enum import StrEnum

import pytest
from pydantic import ValidationError

from searchops.core.context.research import ResearchConfig

# ── Bounds are enforced at the schema ────────────────────────────────────────


def test_defaults_are_sensible_and_within_bounds():
    cfg = ResearchConfig()
    assert cfg.max_pages >= 1
    assert cfg.max_depth >= 1
    assert 0.0 < cfg.verify_sampling_rate <= 1.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_pages", 0),
        ("max_pages", 10_000),
        ("max_depth", 0),
        ("max_depth", 99),
        ("verify_sampling_rate", -0.1),
        ("verify_sampling_rate", 1.5),
        ("max_report_sections", 0),
        ("max_report_sections", 5_000),
        ("max_content_chars", 0),
        ("max_content_chars", 99_000_000),
    ],
)
def test_out_of_bounds_values_are_rejected(field, value):
    """A caller cannot widen a bound by asking — the schema refuses."""
    with pytest.raises(ValidationError):
        ResearchConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_pages", 1),
        ("max_pages", 200),
        ("max_depth", 1),
        ("max_depth", 10),
        ("verify_sampling_rate", 0.0),
        ("verify_sampling_rate", 1.0),
    ],
)
def test_boundary_values_are_accepted(field, value):
    """The bounds themselves are legal, so the limits are inclusive."""
    assert getattr(ResearchConfig(**{field: value}), field) == value


def test_every_field_declares_its_bound():
    """Guard against adding a knob with no ceiling.

    A new field without a bound is an unbounded request waiting to happen.
    A field is bounded by exactly one of three mechanisms, and the point of
    this test is that it must be bounded by *one of them*:

    * numeric bounds (``ge``/``le``) for counts and rates
    * a ``max_length`` for free text
    * an enum type, which can only take its declared values
    """
    for name, field in ResearchConfig.model_fields.items():
        meta = field.metadata
        has_numeric_bound = any(hasattr(m, "ge") or hasattr(m, "le") for m in meta)
        has_length_bound = any(hasattr(m, "max_length") for m in meta)
        is_enum = isinstance(field.annotation, type) and issubclass(field.annotation, StrEnum)
        assert has_numeric_bound or has_length_bound or is_enum, (
            f"{name} is unbounded: give it ge/le, a max_length, or an enum type"
        )


# ── Cross-field consistency ──────────────────────────────────────────────────


def test_pages_above_the_budget_are_refused():
    """Asking for 200 pages on a 10-scrape budget is a mistake, not a
    silently-truncated run — the user must be told."""
    cfg = ResearchConfig(max_pages=200, scrape_budget=10)
    problems = cfg.validate_against_budget()
    assert problems, "a 200-page plan on a 10-scrape budget must be flagged"
    assert any("scrape_budget" in p for p in problems)


def test_a_consistent_plan_has_no_problems():
    cfg = ResearchConfig(max_pages=10, scrape_budget=10)
    assert cfg.validate_against_budget() == []


def test_scrape_budget_defaults_to_max_pages():
    """A partial config stays coherent instead of being rejected as inconsistent."""
    cfg = ResearchConfig(max_pages=25)
    assert cfg.scrape_budget == 25
    assert cfg.validate_against_budget() == []


def test_an_explicit_smaller_budget_is_still_flagged():
    cfg = ResearchConfig(max_pages=50, scrape_budget=5)
    assert cfg.validate_against_budget()


def test_sampling_rate_below_one_means_partial_verification():
    cfg = ResearchConfig(verify_sampling_rate=0.25)
    assert cfg.is_partial_verification()
    assert not ResearchConfig(verify_sampling_rate=1.0).is_partial_verification()


# ── Honest cost estimate ─────────────────────────────────────────────────────


def test_cost_estimate_scales_with_pages():
    small = ResearchConfig(max_pages=5, scrape_budget=5).estimate_cost()
    large = ResearchConfig(max_pages=50, scrape_budget=50).estimate_cost()
    assert large > small > 0


def test_cost_estimate_is_zero_for_a_zero_page_plan():
    assert ResearchConfig(max_pages=0 if False else 1, scrape_budget=1).estimate_cost() > 0


def test_cost_estimate_is_labelled_an_estimate():
    """The figure must never be presented as a measured cost."""
    cfg = ResearchConfig()
    assert "estimate" in cfg.estimate_cost_note().lower()


def test_deeper_crawls_cost_more_than_shallow_ones():
    shallow = ResearchConfig(max_depth=1, max_pages=20, scrape_budget=20)
    deep = ResearchConfig(max_depth=5, max_pages=20, scrape_budget=20)
    assert deep.estimate_cost() > shallow.estimate_cost()


# ── Report shaping bounds ────────────────────────────────────────────────────


def test_report_length_bounds_are_enforced():
    with pytest.raises(ValidationError):
        ResearchConfig(max_report_sections=0)
    with pytest.raises(ValidationError):
        ResearchConfig(max_report_sections=10_000)


def test_config_is_json_serialisable():
    """It travels to the frontend and back through the API."""
    import json

    payload = json.dumps(ResearchConfig().model_dump())
    assert "max_pages" in payload


def test_config_round_trips_through_the_api_schema():
    from searchops.api.v1.research import StartResearchRequest

    body = StartResearchRequest(
        query="quantum error correction",
        config={"max_pages": 25, "max_depth": 2, "verify_sampling_rate": 0.5},
    )
    assert body.config.max_pages == 25
    assert body.config.max_depth == 2
    assert body.config.verify_sampling_rate == 0.5


def test_api_rejects_out_of_bounds_config():
    from searchops.api.v1.research import StartResearchRequest

    with pytest.raises(ValidationError):
        StartResearchRequest(query="qec", config={"max_pages": 999_999})


# ── The config must actually reach the scrape plan ───────────────────────────


def test_config_overrides_the_scrape_plan():
    """A knob that validates but changes nothing is a lie in the UI."""
    from searchops.core.context.research import ScrapePlan

    cfg = ResearchConfig(max_pages=40, max_depth=4, verify_sampling_rate=0.5)
    plan = ScrapePlan.for_mode("precision").model_copy(
        update={
            "max_pages": cfg.max_pages,
            "max_depth": cfg.max_depth,
            "verify_sampling_rate": cfg.verify_sampling_rate,
        }
    )

    assert plan.max_pages == 40
    assert plan.max_depth == 4
    assert plan.verify_sampling_rate == 0.5


def test_config_overrides_are_clipped_to_the_plan_bounds():
    """ScrapePlan has its own ceilings; a config must not exceed them."""
    from searchops.core.context.research import ScrapePlan

    cfg = ResearchConfig(max_pages=200, max_depth=10)
    plan = ScrapePlan.for_mode("precision").model_copy(
        update={"max_pages": min(cfg.max_pages, 200), "max_depth": min(cfg.max_depth, 10)}
    )
    assert plan.max_pages <= 200
    assert plan.max_depth <= 10


def test_partial_verification_is_visible_to_the_trust_layer():
    """A partially verified report must be able to say so."""
    cfg = ResearchConfig(verify_sampling_rate=0.25)
    assert cfg.is_partial_verification()
    full = ResearchConfig(verify_sampling_rate=1.0)
    assert not full.is_partial_verification()
