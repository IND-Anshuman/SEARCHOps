"""
Real-time Token and Cost Budget Tracking Engine.

Price table covers all six supported providers.
Sources (as of July 2026):
  OpenAI    → platform.openai.com/pricing
  Anthropic → anthropic.com/pricing
  Google    → ai.google.dev/pricing
  NVIDIA    → build.nvidia.com/pricing (NIM)
  Bedrock   → aws.amazon.com/bedrock/pricing
  Zhipu     → open.bigmodel.cn/pricing
"""

from __future__ import annotations

import structlog

from searchops.core.context.execution import ExecutionContext
from searchops.core.exceptions.application import BudgetExceededError

log = structlog.get_logger(__name__)

# Cost per single token in USD  (price_per_1M / 1_000_000)
_MODEL_COST_TABLE: dict[str, dict[str, float]] = {
    # ── OpenAI ─────────────────────────────────────────────────────────────
    "gpt-4o":                       {"prompt": 2.50 / 1_000_000, "completion": 10.00 / 1_000_000},
    "gpt-4o-mini":                  {"prompt": 0.15 / 1_000_000, "completion":  0.60 / 1_000_000},
    "gpt-4.1":                      {"prompt": 2.00 / 1_000_000, "completion":  8.00 / 1_000_000},
    "gpt-4.1-mini":                 {"prompt": 0.40 / 1_000_000, "completion":  1.60 / 1_000_000},
    "o1":                           {"prompt": 15.00 / 1_000_000, "completion": 60.00 / 1_000_000},
    "o1-mini":                      {"prompt": 1.10 / 1_000_000, "completion":  4.40 / 1_000_000},
    "o3":                           {"prompt": 10.00 / 1_000_000, "completion": 40.00 / 1_000_000},
    "o3-mini":                      {"prompt": 1.10 / 1_000_000, "completion":  4.40 / 1_000_000},
    "o4-mini":                      {"prompt": 1.10 / 1_000_000, "completion":  4.40 / 1_000_000},

    # ── Anthropic ──────────────────────────────────────────────────────────
    "claude-opus-4-5":              {"prompt": 15.00 / 1_000_000, "completion": 75.00 / 1_000_000},
    "claude-sonnet-4-5":            {"prompt":  3.00 / 1_000_000, "completion": 15.00 / 1_000_000},
    "claude-haiku-4-5":             {"prompt":  0.80 / 1_000_000, "completion":  4.00 / 1_000_000},
    "claude-3-5-sonnet-20241022":   {"prompt":  3.00 / 1_000_000, "completion": 15.00 / 1_000_000},
    "claude-3-5-haiku-20241022":    {"prompt":  0.80 / 1_000_000, "completion":  4.00 / 1_000_000},
    "claude-3-opus-20240229":       {"prompt": 15.00 / 1_000_000, "completion": 75.00 / 1_000_000},

    # ── Google AI Studio / Vertex (Gemini) ─────────────────────────────────
    "gemini-2.5-pro":               {"prompt":  1.25 / 1_000_000, "completion": 10.00 / 1_000_000},
    "gemini-2.5-flash":             {"prompt":  0.30 / 1_000_000, "completion":  2.50 / 1_000_000},
    "gemini-2.5-flash-lite":        {"prompt":  0.10 / 1_000_000, "completion":  0.40 / 1_000_000},
    "gemini-2.0-flash":             {"prompt":  0.10 / 1_000_000, "completion":  0.40 / 1_000_000},
    "gemini-1.5-pro":               {"prompt":  1.25 / 1_000_000, "completion":  5.00 / 1_000_000},
    "gemini-1.5-flash":             {"prompt":  0.075 / 1_000_000, "completion": 0.30 / 1_000_000},

    # ── NVIDIA NIM ─────────────────────────────────────────────────────────
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": {"prompt": 4.50 / 1_000_000, "completion": 18.00 / 1_000_000},
    "nvidia/llama-3.3-nemotron-super-49b-v1":  {"prompt": 0.70 / 1_000_000, "completion":  2.80 / 1_000_000},
    "nvidia/llama-3.1-70b-instruct":           {"prompt": 0.35 / 1_000_000, "completion":  0.40 / 1_000_000},
    "nvidia/mistral-nemo-12b-instruct":        {"prompt": 0.15 / 1_000_000, "completion":  0.15 / 1_000_000},

    # ── Amazon Bedrock ─────────────────────────────────────────────────────
    "anthropic.claude-3-5-sonnet-20241022-v2:0": {"prompt": 3.00 / 1_000_000, "completion": 15.00 / 1_000_000},
    "anthropic.claude-3-haiku-20240307-v1:0":    {"prompt": 0.25 / 1_000_000, "completion":  1.25 / 1_000_000},
    "amazon.nova-pro-v1:0":                      {"prompt": 0.80 / 1_000_000, "completion":  3.20 / 1_000_000},
    "amazon.nova-lite-v1:0":                     {"prompt": 0.06 / 1_000_000, "completion":  0.24 / 1_000_000},
    "meta.llama3-70b-instruct-v1:0":             {"prompt": 0.99 / 1_000_000, "completion":  0.99 / 1_000_000},

    # ── Zhipu AI / Z.AI (GLM) ─────────────────────────────────────────────
    "glm-4-plus":                   {"prompt": 0.14 / 1_000_000, "completion": 0.14 / 1_000_000},
    "glm-4-air":                    {"prompt": 0.014 / 1_000_000, "completion": 0.014 / 1_000_000},
    "glm-4-flash":                  {"prompt": 0.0 / 1_000_000, "completion": 0.0 / 1_000_000},  # free tier
    "glm-4v-plus":                  {"prompt": 0.14 / 1_000_000, "completion": 0.14 / 1_000_000},
}

# Fallback for unknown models — assume mid-range pricing
_DEFAULT_COST = {"prompt": 1.00 / 1_000_000, "completion": 3.00 / 1_000_000}


class LLMBudgetTracker:
    """Enforces execution token & USD cost limits across all providers."""

    @staticmethod
    def calculate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate USD cost for a given model and token counts."""
        rates = _MODEL_COST_TABLE.get(model_name, _DEFAULT_COST)
        return (prompt_tokens * rates["prompt"]) + (completion_tokens * rates["completion"])

    @classmethod
    def record_and_check(
        cls,
        context: ExecutionContext,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Record usage against the execution budget and raise if any limit is breached."""
        total_tokens = prompt_tokens + completion_tokens
        cost_usd = cls.calculate_cost(model_name, prompt_tokens, completion_tokens)

        context.budget.record_usage(tokens=total_tokens, cost_usd=cost_usd)

        log.debug(
            "Recorded LLM usage",
            model=model_name,
            total_tokens=total_tokens,
            cost_usd=round(cost_usd, 6),
            consumed_tokens=context.budget.consumed_tokens,
            consumed_cost=round(context.budget.consumed_cost_usd, 4),
        )

        if context.budget.is_tokens_exceeded:
            raise BudgetExceededError(
                budget_type="Token",
                limit=context.budget.max_tokens,
                consumed=context.budget.consumed_tokens,
            )

        if context.budget.is_cost_exceeded:
            raise BudgetExceededError(
                budget_type="Cost (USD)",
                limit=context.budget.max_cost_usd,
                consumed=context.budget.consumed_cost_usd,
            )

        return cost_usd
