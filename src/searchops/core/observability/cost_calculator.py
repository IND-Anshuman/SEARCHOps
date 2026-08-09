"""
LLM Cost Calculator.

Computes real inference costs from token counts and provider/model metadata.
Pricing is sourced from the settings configuration — never hardcoded in
business logic.

Usage:
    cost = calculate_cost("google", "gemini-1.5-flash", input_tokens=1500, output_tokens=800)
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# Pricing table: (input_usd_per_1m, output_usd_per_1m)
# Sources: provider published pricing pages (2025-Q1).
# Update this table as providers change pricing; never hardcode in service code.
_PRICING: dict[str, tuple[float, float]] = {
    # Google Gemini
    "gemini-1.5-flash":        (0.075,  0.30),
    "gemini-1.5-flash-8b":     (0.0375, 0.15),
    "gemini-1.5-pro":          (1.25,   5.00),
    "gemini-2.0-flash":        (0.10,   0.40),
    "gemini-2.5-flash":        (0.15,   0.60),
    "gemini-2.5-pro":          (1.25,   10.00),
    # OpenAI
    "gpt-4o":                  (2.50,   10.00),
    "gpt-4o-mini":             (0.15,   0.60),
    "gpt-4-turbo":             (10.00,  30.00),
    "gpt-3.5-turbo":           (0.50,   1.50),
    # Anthropic
    "claude-3-5-sonnet":       (3.00,   15.00),
    "claude-3-5-haiku":        (0.80,   4.00),
    "claude-3-opus":           (15.00,  75.00),
    # NVIDIA / Llama
    "llama-3.1-405b-instruct": (5.00,   16.00),
    "llama-3.1-70b-instruct":  (0.35,   0.40),
    # Fallback blended rate for unknown models
    "_default":                (1.00,   3.00),
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calculate estimated inference cost in USD.

    Args:
        model: Model name as returned by the LLM provider (e.g. 'gemini-1.5-flash').
        input_tokens: Number of input/prompt tokens consumed.
        output_tokens: Number of output/completion tokens generated.

    Returns:
        Estimated cost in USD, rounded to 6 decimal places.
    """
    # Normalize model name: strip provider prefix if present (e.g. 'google/gemini-1.5-flash')
    normalized = model.split("/")[-1].lower().strip()

    input_rate, output_rate = _PRICING.get(normalized, _PRICING["_default"])

    if normalized not in _PRICING:
        log.warning(
            "Unknown model in cost calculator; using default rate",
            model=model,
            normalized=normalized,
        )

    cost = (input_tokens * input_rate / 1_000_000) + (output_tokens * output_rate / 1_000_000)
    return round(cost, 6)
