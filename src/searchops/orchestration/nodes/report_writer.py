"""Report Writer node: synthesises all evidence into a structured Markdown report.

The report is written one section per LLM call, not in a single request.

A single call cannot produce a long report. Output is bounded by max_tokens
(4096 by default) — roughly two pages of prose regardless of how much evidence
is supplied — so asking for 25 pages in one request returns a truncated section
that stops mid-sentence. Sectioned generation removes that ceiling: report
length becomes a function of the evidence, not of one response's token limit.

Each section call receives the evidence budget independently, so sections are
not starved by sharing a single pool, and a section whose call fails is skipped
without discarding the sections that succeeded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from searchops.llm.report_shaper import shape_report
from searchops.llm.token_budget import (
    REPORT_SECTIONS,
    SECTION_SYSTEM_PROMPT,
    build_report_prompt,
    build_section_prompt,
)

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

# Per-section output ceiling. Higher than a whole-report call's would be,
# because one section does not have to leave room for seven others.
_SECTION_MAX_TOKENS = 3000


async def report_writer_node(
    state: ResearchState,
    *,
    llm_router: LLMRouter,
) -> ResearchState:
    """Synthesise scraped evidence into a sectioned Markdown report.

    Sections are generated sequentially and concatenated. The assembled text is
    still passed through ``shape_report`` so a refusal, an empty reply, or
    unstructured prose is handled honestly and the UI can explain a degraded
    report rather than presenting it as model-written.
    """
    query = state.get("query", "")
    scraped = state.get("scraped_contents", [])
    entities = state.get("entities", [])
    claims = state.get("evidence_claims", [])
    log.info("Report writer node executing", query=query, sources=len(scraped))

    # Outline first, so each section call knows what the others cover and does
    # not repeat them.
    system_prompt, outline_prompt = build_report_prompt(query=query, entities=entities, scraped=scraped)
    outline = ""
    if scraped:
        try:
            outline = await llm_router.generate(
                prompt=outline_prompt,
                system_prompt=(
                    "You are a principal research analyst. Produce ONLY a "
                    "Markdown outline of section titles (## level) with a "
                    "one-line scope note each, covering the evidence provided. "
                    "No prose paragraphs."
                ),
                temperature=0.2,
                max_tokens=1200,
            )
        except Exception:
            log.warning("report_writer.outline_failed")

    sections: list[str] = []
    failed: list[str] = []

    for section in REPORT_SECTIONS:
        section_prompt = build_section_prompt(
            query=query,
            section=section,
            entities=entities,
            scraped=scraped,
            outline=outline,
        )
        try:
            written = await llm_router.generate(
                prompt=section_prompt,
                system_prompt=SECTION_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=_SECTION_MAX_TOKENS,
            )
        except Exception:
            log.warning("report_writer.section_failed", section=section)
            failed.append(section)
            continue

        text = (written or "").strip()
        if not text:
            failed.append(section)
            continue

        # Guarantee the heading survives even if the model omitted it, so the
        # section stays navigable and the block model can address it.
        if not text.lstrip().startswith("#"):
            text = f"## {section}\n\n{text}"
        sections.append(text)

    if not sections:
        # Every section call failed or returned nothing. Fall back to a single
        # whole-report call so the run still produces something, and shape_report
        # records honestly what happened. If that call fails too, the evidence
        # itself is still surfaced — a report built from the real sources is
        # better than the node raising and the job reporting failure.
        log.warning("report_writer.all_sections_empty; falling back to single call")
        try:
            raw = await llm_router.generate(
                prompt=outline_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=4096,
            )
        except Exception:
            log.warning("report_writer.fallback_failed; shaping from evidence only")
            raw = ""
        shaped = shape_report(raw, query=query, claims=claims, entities=entities, sources=scraped)
        return {
            "final_report": shaped.markdown,
            "citations": [d.get("url", "") for d in scraped if d.get("url")],
            "report_sections": [shaped.markdown],
            "report_quality": shaped.quality.value,
            "report_user_message": shaped.user_message,
        }

    combined = f"# Research Report\n\n_Query: {query}_\n\n" + "\n\n".join(sections)
    if failed:
        combined += (
            "\n\n---\n\n**Note:** the following sections could not be generated "
            f"from the available evidence: {', '.join(failed)}.\n"
        )

    shaped = shape_report(combined, query=query, claims=claims, entities=entities, sources=scraped)

    log.info(
        "report_writer.sectioned_complete",
        sections_written=len(sections),
        sections_failed=len(failed),
        chars=len(shaped.markdown),
    )

    if shaped.is_synthesized or shaped.quality.value != "ok":
        log.warning(
            "report_writer.shaped",
            quality=shaped.quality.value,
            sections=shaped.sections,
            citations=shaped.citation_count,
            notes=shaped.notes,
        )

    return {
        "final_report": shaped.markdown,
        "citations": [d.get("url", "") for d in scraped if d.get("url")],
        "report_sections": sections,
        "report_quality": shaped.quality.value,
        "report_user_message": shaped.user_message,
    }
