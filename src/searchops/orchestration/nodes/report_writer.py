"""
Report Writer node: synthesises all evidence into a structured Markdown report.

Free-tier note: prompt is assembled via build_report_prompt() which caps
entity rows, document excerpts, and total tokens before sending to the LLM.
"""

from __future__ import annotations

import structlog

from searchops.llm.router import LLMRouter
from searchops.llm.token_budget import build_report_prompt
from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


async def report_writer_node(
    state: ResearchState,
    *,
    llm_router: LLMRouter,
) -> ResearchState:
    """Synthesise scraped evidence into a concise Markdown report.

    The prompt is pre-capped by build_report_prompt() to respect
    free-tier token quotas before the LLM call is made.
    """
    query = state.get("query", "")
    scraped = state.get("scraped_contents", [])
    entities = state.get("entities", [])
    log.info("Report writer node executing", query=query, sources=len(scraped))

    # build_report_prompt emits system_prompt and user_prompt tuple for prompt caching
    system_prompt, user_prompt = build_report_prompt(query=query, entities=entities, scraped=scraped)

    report = await llm_router.generate(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=4096,
    )
    citations = [doc.get("url", "") for doc in scraped if doc.get("url")]

    return {"final_report": report, "citations": citations, "report_sections": [report]}  # type: ignore[misc]

