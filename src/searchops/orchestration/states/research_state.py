"""
Typed State for the Deep Research LangGraph workflow.

All fields are Optional so LangGraph can emit partial updates; the
graph merges deltas rather than replacing the whole state dict.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages

from searchops.core.context.research import ExecutionBudget, ResearchDepth, ResearchPlan, SearchExecution
from searchops.knowledge.domain.entity import KGEntity, KGRelation
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
        key = f"{rel.source_canonical_id or rel.source_id}:{rel.relation_type}:{rel.target_canonical_id or rel.target_id}"
        if key not in seen:
            seen[key] = rel
    return list(seen.values())


class ResearchState(TypedDict, total=False):
    """Shared domain state propagated across every node in the deep-research graph."""

    # ── Input & Plan ──────────────────────────────────────────────────────
    query: str
    depth: ResearchDepth
    max_sources: int
    correlation_id: str
    plan: ResearchPlan
    budget: ExecutionBudget

    # ── Search & Telemetry ────────────────────────────────────────────────
    search_results: Annotated[list[SearchResultItem], operator.add]
    search_executions: Annotated[list[SearchExecution], operator.add]
    urls_to_scrape: Annotated[list[str], operator.add]

    # ── Scraping ──────────────────────────────────────────────────────────
    scraped_contents: Annotated[list[dict[str, Any]], replace_list]   # [{"url": str, "content": str, "title": str}]
    failed_urls: Annotated[list[str], operator.add]

    # ── Knowledge Extraction ──────────────────────────────────────────────
    entities: Annotated[list[KGEntity], dedupe_entities]
    relations: Annotated[list[KGRelation], dedupe_relations]

    # ── Report ────────────────────────────────────────────────────────────
    report_sections: Annotated[list[str], operator.add]
    final_report: str
    citations: Annotated[list[str], operator.add]

    # ── Agent messaging (LangGraph convention) ────────────────────────────
    messages: Annotated[list[Any], add_messages]

    # ── Metadata ──────────────────────────────────────────────────────────
    error: str | None
    iteration: int


