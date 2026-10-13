"""
Token-efficient text utilities for free-tier LLM usage.

All prompt-building helpers live here so truncation logic is
never scattered across node files.
"""

from __future__ import annotations

from typing import Any

from searchops.llm.tokenizer import count_tokens, truncate_by_tokens

# Budget allocation for report synthesis
MAX_PROMPT_TOKENS = 24_000  # supports modern LLM context windows
MAX_DOC_CHARS = 4_000  # per scraped document fed into the report prompt
MAX_ENTITY_ROWS = 50  # entity lines in the report prompt
MAX_EXCERPT_DOCS = 30  # documents shown in the report prompt


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


def build_report_prompt(
    query: str,
    entities: list[Any],
    scraped: list[Any],
    model_name: str = "gpt-4o",
) -> tuple[str, str]:
    """Compose (system_prompt, user_prompt) tuple, allocating budget across sections without severing context."""
    system_prompt = (
        "You are a principal research scientist and technical analyst. "
        "Write an exhaustive, authoritative, and well-structured research report "
        "addressing the query in deep detail based on the evidence provided.\n\n"
        "Requirements:\n"
        "1. Structure the report with clear Markdown headers (# for Title, ## for Main Sections, ### for Subsections).\n"
        "2. Include: Executive Summary, Comprehensive Background & Landscape, "
        "Architectural & Technical Deep Dive, Comparative Analysis, Real-World Case Studies & Data, "
        "Security & Operational Implications, and Strategic Recommendations.\n"
        "3. Provide rich, detailed explanations with concrete numbers, trade-offs, and examples. "
        "Do NOT write a brief high-level overview. Deliver a thorough, publication-grade document.\n"
        "4. Seamlessly cite the provided source URLs inline where relevant."
    )

    entity_lines = (
        "\n".join(
            f"- {e.name} ({getattr(e, 'entity_type', 'Entity')}): {getattr(e, 'description', '')}"
            for e in entities[:MAX_ENTITY_ROWS]
        )
        or "None extracted"
    )

    header_text = f"Query: {query}\n\nKey Entities:\n{entity_lines}\n\nSource Excerpts:\n"
    header_tokens = count_tokens(header_text, model_name)
    remaining_tokens = max(500, MAX_PROMPT_TOKENS - header_tokens)

    per_doc_tokens = max(100, remaining_tokens // max(1, min(len(scraped), MAX_EXCERPT_DOCS)))

    excerpts_list = [build_scrape_excerpt(doc, max_tokens=per_doc_tokens) for doc in scraped[:MAX_EXCERPT_DOCS]]
    excerpts = "\n\n---\n\n".join(excerpts_list) or "No content scraped"

    user_prompt = f"{header_text}{excerpts}"
    return system_prompt, user_prompt
