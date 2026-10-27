"""
Unit tests for per-node token-economy & LLM accounting metrics.

Verifies that the new per-node metrics (NODE_*) are importable, carry the
expected labels, and can be incremented/observed end-to-end without error.
These are the probes that let ops answer "which graph node consumed the most
tokens" and whether caching actually reduced live API hits.
"""

from __future__ import annotations

import pytest

from searchops.core.observability.metrics import (
    EXTRACT_CLAIMS_TOTAL,
    NODE_INVOCATIONS_TOTAL,
    NODE_LLM_COST_USD_TOTAL,
    NODE_LLM_REQUEST_DURATION_SECONDS,
    NODE_LLM_TOKENS_IN_TOTAL,
    NODE_LLM_TOKENS_OUT_TOTAL,
)


@pytest.mark.unit
def test_node_metrics_importable():
    """All per-node metrics must be importable (non-None)."""
    assert NODE_INVOCATIONS_TOTAL is not None
    assert NODE_LLM_TOKENS_IN_TOTAL is not None
    assert NODE_LLM_TOKENS_OUT_TOTAL is not None
    assert NODE_LLM_COST_USD_TOTAL is not None
    assert NODE_LLM_REQUEST_DURATION_SECONDS is not None
    assert EXTRACT_CLAIMS_TOTAL is not None


@pytest.mark.unit
def test_node_invocations_counter_labels():
    """Invocations counter accepts (node, outcome) labels and increments."""
    before = NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="ok")._value.get()
    NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="ok").inc()
    after = NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="ok")._value.get()
    assert after == before + 1


@pytest.mark.unit
def test_node_llm_tokens_in_counter_cache_hit_label():
    """Token-in counter accepts (node, cache_hit) labels; cache hits tracked separately."""
    NODE_LLM_TOKENS_IN_TOTAL.labels(node="extract_claims", cache_hit="false").inc(1200)
    NODE_LLM_TOKENS_IN_TOTAL.labels(node="extract_claims", cache_hit="true").inc(0)
    fresh = NODE_LLM_TOKENS_IN_TOTAL.labels(node="extract_claims", cache_hit="false")._value.get()
    cached = NODE_LLM_TOKENS_IN_TOTAL.labels(node="extract_claims", cache_hit="true")._value.get()
    assert fresh >= 1200
    assert cached == 0


@pytest.mark.unit
def test_extract_claims_total_labels_and_inc():
    """Evidence-claim counter accepts (node, status) labels."""
    NODE_LLM_TOKENS_OUT_TOTAL.labels(node="report_writer").inc(512)
    assert NODE_LLM_TOKENS_OUT_TOTAL.labels(node="report_writer")._value.get() >= 512

    EXTRACT_CLAIMS_TOTAL.labels(node="extract_claims", status="extracted").inc()
    assert EXTRACT_CLAIMS_TOTAL.labels(node="extract_claims", status="extracted")._value.get() >= 1


@pytest.mark.unit
def test_node_llm_cost_usd_histogram_api():
    """Cost counter and duration histogram accept .labels().inc()/.observe() and stay additive."""
    NODE_LLM_COST_USD_TOTAL.labels(node="verifier").inc(0.0004)
    assert NODE_LLM_COST_USD_TOTAL.labels(node="verifier")._value.get() >= 0.0004
    NODE_LLM_REQUEST_DURATION_SECONDS.labels(node="planner").observe(1.5)
    assert True  # observe() must not raise
