"""
LLM Model Cost & Latency Evaluator.

Maintains model pricing matrices ($/1M tokens) and performance scores
to select the cost-optimal model for a given LLM task tier.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Model pricing in USD per 1,000,000 tokens (Input / Output)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}


class LLMCostEvaluator:
    """Evaluates token cost and selects the optimal provider model."""

    def __init__(self, pricing_matrix: dict[str, tuple[float, float]] | None = None) -> None:
        self.pricing_matrix = pricing_matrix or _MODEL_PRICING

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate execution cost in USD for a given model and token count."""
        model_key = model.lower()
        if model_key not in self.pricing_matrix:
            # Fallback average pricing
            input_rate, output_rate = 1.00, 4.00
        else:
            input_rate, output_rate = self.pricing_matrix[model_key]

        input_cost = (input_tokens / 1_000_000) * input_rate
        output_cost = (output_tokens / 1_000_000) * output_rate
        return round(input_cost + output_cost, 6)

    def select_cheapest_model(self, candidates: list[str], input_tokens: int = 1000, output_tokens: int = 500) -> str:
        """Select the model with lowest estimated cost from a list of candidate models."""
        if not candidates:
            return "gpt-4o-mini"

        best_model = candidates[0]
        lowest_cost = float("inf")

        for model in candidates:
            cost = self.estimate_cost(model, input_tokens, output_tokens)
            if cost < lowest_cost:
                lowest_cost = cost
                best_model = model

        return best_model
