"""
Unit tests for LLMCostEvaluator and LLMRouter fallback cascade.
"""

from __future__ import annotations

import pytest
from searchops.llm.cost_evaluator import LLMCostEvaluator


@pytest.mark.unit
def test_cost_evaluator_pricing():
    evaluator = LLMCostEvaluator()
    cost_gpt4o = evaluator.estimate_cost("gpt-4o", 100_000, 10_000)
    assert cost_gpt4o == round((0.1 * 2.50) + (0.01 * 10.00), 6)

    cost_flash = evaluator.estimate_cost("gemini-1.5-flash", 100_000, 10_000)
    assert cost_flash < cost_gpt4o


@pytest.mark.unit
def test_cost_evaluator_select_cheapest():
    evaluator = LLMCostEvaluator()
    candidates = ["gpt-4o", "gemini-1.5-flash", "claude-3-5-sonnet"]
    cheapest = evaluator.select_cheapest_model(candidates, input_tokens=50_000, output_tokens=5_000)
    assert cheapest == "gemini-1.5-flash"


@pytest.mark.unit
def test_llm_router_provider_detection():
    from searchops.llm.router import LLMRouter, _is_claude, _is_gemini, _is_nvidia, _is_bedrock, _is_glm

    assert _is_claude("claude-3-haiku") is True
    assert _is_gemini("gemini-2.0-flash") is True
    assert _is_nvidia("nvidia/llama-3") is True
    assert _is_bedrock("anthropic.claude-3-haiku-v1:0") is True
    assert _is_glm("glm-4-flash") is True

    router = LLMRouter()
    assert router._resolve_provider_label("gpt-4o-mini") == "openai"
    assert router._resolve_provider_label("claude-3-haiku") == "anthropic"
    assert router._resolve_provider_label("gemini-1.5-flash") == "google"

