"""
Token-efficient text utilities for free-tier LLM usage.

All prompt-building helpers live here so truncation logic is
never scattered across node files.
"""

from __future__ import annotations

from typing import Any

from searchops.llm.tokenizer import truncate_by_tokens

# Budget allocation for report synthesis
MAX_PROMPT_TOKENS = 24_000  # supports modern LLM context windows
MAX_DOC_CHARS = 4_000  # per scraped document fed into the report prompt
MAX_ENTITY_ROWS = 50  # entity lines in the report prompt
MAX_EXCERPT_DOCS = 30  # documents shown in the report prompt

# ── Prompt size contract ─────────────────────────────────────────────────────
#
# The router truncates every prompt to ``LLM_MAX_PROMPT_CHARS`` before sending
# it. That cap is applied AFTER the prompt is built, so a builder that emits
# more than the cap does not get a smaller prompt — it gets a prompt with its
# tail cut off. The tail is where the writing instructions live, so the failure
# was silent and severe: the model received a wall of evidence and no direction,
# and answered with a short, generic section.
#
# These two constants must agree. ``MAX_SECTION_PROMPT_CHARS`` is what the
# builder targets; ``REQUIRED_LLM_MAX_PROMPT_CHARS`` is what the router must
# allow. A test asserts the shipped default satisfies it.
MAX_SECTION_PROMPT_CHARS = 24_000
REQUIRED_LLM_MAX_PROMPT_CHARS = 32_000
_PROMPT_SAFETY_MARGIN = 500  # room for the system prompt and formatting drift
_MIN_EVIDENCE_CHARS = 2_000  # always send some evidence, even in a tight budget


def truncate_to_tokens(text: str, max_tokens: int, model: str = "gpt-4o") -> str:
    """Exact tokenizer-native truncation slicing token IDs via tiktoken."""
    return truncate_by_tokens(text, max_tokens, model)


def build_scrape_excerpt(doc: dict[str, Any], max_tokens: int = 800) -> str:
    """Return a compact excerpt string for a single scraped document with exact token truncation."""
    summary = (doc.get("content_summary") or doc.get("content") or "").strip()
    snippets = doc.get("snippets", [])
    snippet_text = "\n".join(f"- {s}" for s in snippets) if snippets else ""

    combined_body = f"{summary}\n\nKey Snippets:\n{snippet_text}" if snippet_text else summary
    truncated_content = truncate_by_tokens(combined_body, max_tokens)
    return f"URL: {doc.get('url', 'unknown')}\nTitle: {doc.get('title', '')}\n\n{truncated_content}"
