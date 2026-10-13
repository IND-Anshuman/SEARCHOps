"""
Typed State for the Deep Research LangGraph workflow.

All fields are Optional so LangGraph can emit partial updates; the
graph merges deltas rather than replacing the whole state dict.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# NOTE: these are intentionally runtime imports (not in a TYPE_CHECKING block).
# LangGraph calls get_type_hints() on ResearchState at graph-build time, and
# Pydantic resolves KGRelation forward refs at model construction — both need
# the real objects present in module globals.
from searchops.core.context.research import (
    ExecutionBudget,
    ResearchDepth,
    ResearchPlan,
    SearchExecution,
)
from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.orchestration.evidence import Claim, EvidenceGap, dedupe_claims
from searchops.search.contracts import SearchResultItem


def replace_list(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """State reducer that replaces the previous list value with the new list delta if provided."""
    if right is None:
        return left or []
    return right


def dedupe_entities(left: list[KGEntity] | None, right: list[KGEntity] | None) -> list[KGEntity]:
    """State reducer that merges and deduplicates KGEntity lists by canonical_id."""
    combined = (left or []) + (right or [])
    seen: dict[str, KGEntity] = {}
    for entity in combined:
        if entity.canonical_id not in seen:
            seen[entity.canonical_id] = entity
    return list(seen.values())


def dedupe_relations(left: list[KGRelation] | None, right: list[KGRelation] | None) -> list[KGRelation]:
    """State reducer that merges and deduplicates KGRelation lists by unique relation key."""
    combined = (left or []) + (right or [])
    seen: dict[str, KGRelation] = {}
    for rel in combined:
        key = (
            f"{rel.source_canonical_id or rel.source_id}:{rel.relation_type}:{rel.target_canonical_id or rel.target_id}"
        )
        if key not in seen:
            seen[key] = rel
    return list(seen.values())


class ResearchState(TypedDict, total=False):
    """Shared domain state propagated across every node in the deep-research graph."""

    # ── Input & Plan ──────────────────────────────────────────────────────
    query: str
    depth: ResearchDepth
    mode: str
    scrape_plan: dict[str, Any]
    max_sources: int
    correlation_id: str
    plan: ResearchPlan
    budget: ExecutionBudget

    # ── Search & Telemetry ────────────────────────────────────────────────
    search_results: Annotated[list[SearchResultItem], operator.add]
    search_executions: Annotated[list[SearchExecution], operator.add]
    urls_to_scrape: Annotated[list[str], operator.add]

    # ── Scraping ──────────────────────────────────────────────────────────
    scraped_contents: Annotated[list[dict[str, Any]], replace_list]  # [{"url": str, "content": str, "title": str}]
    failed_urls: Annotated[list[str], operator.add]
    # Free counters from the crawl frontier (pages fetched, links seen, URLs
    # dropped as out of scope). Empty in precision mode, where no frontier runs.
    frontier_stats: dict[str, Any]
    # Stage-4 relevance filter counters (pages kept/dropped, degradation reason).
    relevance_stats: dict[str, Any]
    # Stage 7 verification counters: how many claims were verified vs sampled out.
    verification_stats: dict[str, Any]

    # ── Knowledge Extraction ──────────────────────────────────────────────
    entities: Annotated[list[KGEntity], dedupe_entities]
    relations: Annotated[list[KGRelation], dedupe_relations]

    # ── Typed Evidence (claims + coverage gaps) ───────────────────────────
    evidence_claims: Annotated[list[Claim], dedupe_claims]  # merges & dedupes by claim.id
    evidence_gaps: Annotated[list[EvidenceGap], replace_list]  # replaced wholesale each round

    # ── Report ────────────────────────────────────────────────────────────
    report_sections: Annotated[list[str], operator.add]
    final_report: str
    citations: Annotated[list[str], operator.add]

    # ── Report quality (why the report looks the way it does) ─────────────
    # Populated by report_writer via llm/report_shaper.py so the UI can
    # explain an honest degradation instead of showing a blank report.
    report_quality: str
    report_user_message: str

    # ── Agent messaging (LangGraph convention) ────────────────────────────
    messages: Annotated[list[Any], add_messages]

    # ── Metadata ──────────────────────────────────────────────────────────
    error: str | None
    iteration: int
    loop_count: int  # increments per critic→gap_analyst→planner cycle; caps total rounds
