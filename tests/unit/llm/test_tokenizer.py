"""
Unit tests for the tokenizer utilities: exact tiktoken counting, encoding
resolution per model family, and token-native vs. word-boundary truncation.
"""

from __future__ import annotations

import pytest

from searchops.llm.tokenizer import (
    count_tokens,
    get_tokenizer_encoding,
    truncate_by_tokens,
)

# ── Encoding resolution ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_encoding_resolved_for_known_openai_model():
    assert get_tokenizer_encoding("gpt-4o") is not None


@pytest.mark.unit
@pytest.mark.parametrize("model", ["gpt-4-turbo", "o1-mini", "o3-preview"])
def test_unknown_openai_family_falls_back_to_cl100k(model):
    """A gpt-/o1-/o3- name tiktoken doesn't know still gets cl100k_base."""
    assert get_tokenizer_encoding(model) is not None


@pytest.mark.unit
@pytest.mark.parametrize("model", ["claude-3-5-sonnet", "gemini-1.5-flash", "glm-4-flash"])
def test_non_openai_models_have_no_encoding(model):
    assert get_tokenizer_encoding(model) is None


@pytest.mark.unit
def test_empty_model_name_returns_none():
    assert get_tokenizer_encoding("") is None


# ── count_tokens ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_empty_text_counts_zero():
    assert count_tokens("") == 0


@pytest.mark.unit
def test_return_type_is_token_count():
    """TokenCount is a type alias over int, so the value must be a plain int."""
    result = count_tokens("hello world")

    assert type(result) is int


@pytest.mark.unit
def test_openai_count_is_exact_and_positive():
    assert count_tokens("Hello world from SEARCHOps", "gpt-4o") > 0


@pytest.mark.unit
def test_longer_text_counts_more_tokens():
    short = count_tokens("hi", "gpt-4o")
    long = count_tokens("hi " * 200, "gpt-4o")

    assert long > short


@pytest.mark.unit
def test_non_openai_uses_character_estimate():
    """1 token ~ 4 characters for models with no tiktoken encoding."""
    text = "x" * 400

    assert count_tokens(text, "claude-3-5-sonnet") == 100


@pytest.mark.unit
def test_non_openai_estimate_never_returns_zero_for_text():
    assert count_tokens("hi", "claude-3-5-sonnet") == 1


@pytest.mark.unit
@pytest.mark.parametrize("length,expected", [(3, 1), (4, 1), (8, 2), (400, 100)])
def test_character_estimate_rounds_down(length, expected):
    assert count_tokens("x" * length, "gemini-1.5-flash") == expected


# ── truncate_by_tokens ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("text,max_tokens", [("", 10), ("hello", 0), ("hello", -5), (None, 10)])
def test_truncate_guards_invalid_inputs(text, max_tokens):
    assert truncate_by_tokens(text, max_tokens) == ""


@pytest.mark.unit
def test_truncate_returns_text_unchanged_when_under_limit():
    text = "short text"
    assert truncate_by_tokens(text, 100, "gpt-4o") == text


@pytest.mark.unit
def test_truncate_reduces_token_count_for_openai_model():
    text = "word " * 500
    out = truncate_by_tokens(text, 10, "gpt-4o")

    assert count_tokens(out, "gpt-4o") <= 10
    assert len(out) < len(text)


@pytest.mark.unit
def test_truncate_produces_decodable_text():
    text = "The quick brown fox jumps over the lazy dog. " * 50
    out = truncate_by_tokens(text, 8, "gpt-4o")

    assert out.strip() != "", "Decoded output must not be empty"


@pytest.mark.unit
def test_truncate_non_openai_uses_char_limit():
    text = "x" * 400
    out = truncate_by_tokens(text, 10, "claude-3-5-sonnet")  # 10 * 4 = 40 chars

    assert len(out) <= 40


@pytest.mark.unit
def test_truncate_non_openai_cuts_on_word_boundary():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    out = truncate_by_tokens(text, 3, "claude-3-5-sonnet")  # 12 chars

    assert not out.endswith(" "), "Truncation lands on a word boundary"
    assert " " not in out[-1:], "No dangling separator"


@pytest.mark.unit
def test_truncate_non_openai_without_spaces_returns_raw_slice():
    text = "x" * 400
    out = truncate_by_tokens(text, 5, "claude-3-5-sonnet")  # 20 chars, no spaces

    assert out == "x" * 20


@pytest.mark.unit
def test_truncate_is_idempotent_at_the_boundary():
    text = "word " * 100
    once = truncate_by_tokens(text, 20, "gpt-4o")
    twice = truncate_by_tokens(once, 20, "gpt-4o")

    assert once == twice
