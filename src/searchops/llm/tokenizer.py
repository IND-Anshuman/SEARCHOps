"""
Exact Token Counting & Tokenizer-Native Truncation using tiktoken.
"""

from __future__ import annotations

import tiktoken

from searchops.typing.newtypes import TokenCount


def get_tokenizer_encoding(model_name: str = "gpt-4o") -> tiktoken.Encoding | None:
    """Return tiktoken encoding for model name or None if non-OpenAI model."""
    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        if "gpt-" in model_name or "o1-" in model_name or "o3-" in model_name:
            return tiktoken.get_encoding("cl100k_base")
        return None


def count_tokens(text: str, model_name: str = "gpt-4o") -> TokenCount:
    """Return exact token count for OpenAI models using tiktoken, or estimation for non-OpenAI models."""
    if not text:
        return TokenCount(0)
    encoding = get_tokenizer_encoding(model_name)
    if encoding is not None:
        return TokenCount(len(encoding.encode(text)))
    # For non-OpenAI models (Claude, Gemini, etc.), 1 token ~ 4 characters
    return TokenCount(max(1, len(text) // 4))


def truncate_by_tokens(text: str, max_tokens: int, model_name: str = "gpt-4o") -> str:
    """Truncate text by tokens with fallback for non-OpenAI models."""
    if not text or max_tokens <= 0:
        return ""

    encoding = get_tokenizer_encoding(model_name)
    if encoding is not None:
        tokens = encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return encoding.decode(tokens[:max_tokens])

    # Character-based fallback truncation at word boundary
    char_limit = max_tokens * 4
    if len(text) <= char_limit:
        return text
    truncated = text[:char_limit]
    space_idx = truncated.rfind(" ")
    return truncated[:space_idx] if space_idx > 0 else truncated
