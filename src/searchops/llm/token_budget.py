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


# ── Sectioned report synthesis ────────────────────────────────────────────────
#
# A single call cannot produce a long report: the output is bounded by
# max_tokens (4096 by default), which is roughly two pages of prose no matter
# how much evidence is in the prompt. Asking a model for a 25-page document in
# one request produces a truncated section and a mid-sentence stop.
#
# Instead the report is written one section at a time. Each call gets the
# evidence relevant to ITS section, so a 30-source corpus is spread across
# calls rather than one call trying to carry all of it and overflowing.
#
# Sections are keyed by name so a failed section can be retried or skipped
# without re-running the sections that succeeded.

REPORT_SECTIONS: tuple[str, ...] = (
    "Executive Summary",
    "Background and Landscape",
    "Technical Deep Dive",
    "Comparative Analysis",
    "Case Studies and Real-World Data",
    "Security and Operational Implications",
    "Risks, Limitations and Open Questions",
    "Strategic Recommendations",
    "Implementation Roadmap",
    "Conclusion",
)


def build_section_prompt(
    query: str,
    section: str,
    entities: list[Any],
    scraped: list[Any],
    *,
    outline: str = "",
    model_name: str = "gpt-4o",
) -> str:
    """Build the user prompt for ONE report section.

    Each section receives the evidence budget independently. That is the whole
    point: a section that gets 800 tokens of its own sources can be written in
    full, where a shared 24k pool spread over eight sections starves every one
    of them.

    Returns just the user prompt. The system prompt is constant across sections
    and supplied by ``SECTION_SYSTEM_PROMPT`` so it can be cached.
    """
    doc_slice = scraped[:MAX_EXCERPT_DOCS]
    excerpts = "\n\n---\n\n".join(build_scrape_excerpt(doc) for doc in doc_slice)
    entity_lines = (
        "\n".join(
            f"- {e.name} ({getattr(e, 'entity_type', 'Entity')}): {getattr(e, 'description', '')}"
            for e in entities[:MAX_ENTITY_ROWS]
        )
        or "None extracted"
    )

    outline_block = f"\nReport outline:\n{outline}\n" if outline else ""

    return (
        f"Query: {query}\n"
        f"{outline_block}\n"
        f"You are writing ONLY the section titled: {section}\n"
        f"Do not write any other section. Do not add a report-level title.\n"
        f"Do NOT repeat the section title inside the body — the heading is added "
        f"for you; start immediately with the prose.\n\n"
        f"Key Entities:\n{entity_lines}\n\n"
        f"Source Excerpts:\n{excerpts}\n\n"
        f"Write the '{section}' section now in Markdown. Aim for 400-700 words of "
        f"specific, substantive prose. The excerpts above ARE your evidence base — "
        f"draw concrete details, trade-offs and examples from them and cite as [n]. "
        f"Only flag a specific point as uncovered if the excerpts genuinely say "
        f"nothing about it, and keep writing everything else."
    )


SECTION_SYSTEM_PROMPT = (
    "You are a principal research scientist writing one section of a "
    "publication-grade research report.\n\n"
    "Rules for this section:\n"
    "1. Open with a '## <section name>' heading.\n"
    "2. Write substantial prose with concrete numbers, trade-offs and examples "
    "drawn from the evidence provided.\n"
    "3. Never invent facts, figures or sources that are not in the evidence.\n"
    "4. Cite the evidence as [1], [2] ... matching the excerpt order.\n"
    "5. Do not summarise the whole report or write other sections.\n"
    "6. The excerpts supplied ARE your evidence base. Work from them: state what "
    "they establish, name the trade-offs they describe, and quote specific "
    "figures or examples where present. Absence of detail is the exception, not "
    "the default response — only note it for a specific point the sources "
    "genuinely do not cover, and keep writing the rest."
)


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
