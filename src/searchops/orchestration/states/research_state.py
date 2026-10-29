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


def merge_by_url(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Accumulate page records across research loops, keyed on ``url``.

    A later record for the same URL replaces the earlier one in place (the
    state pruner blanks page text that way). Replacing the whole list each
    round lost every source fetched in an earlier loop, so claims from round
    one cited pages missing from the report's Sources list.
    """
    merged: dict[str, dict[str, Any]] = {}
    for doc in (left or []) + (right or []):
        if isinstance(doc, dict) and doc.get("url"):
            merged[str(doc["url"])] = doc
    return list(merged.values())


def merge_passages(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Accumulate evidence passages across loops, keyed on passage id."""
    merged: dict[str, Any] = {}
    for p in (left or []) + (right or []):
        pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
        if pid:
            merged[str(pid)] = p
    return list(merged.values())


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
    # Follow-up queries the gap analyst queued for the next search round, and
    # every query already searched (so a loop never re-issues one).
    pending_queries: Annotated[list[str], replace_list]
    searched_queries: Annotated[list[str], operator.add]

    # ── Report configuration (from the request) ───────────────────────────
    # Declared so LangGraph keeps them: keys missing from the schema are
    # silently dropped from the input, which made these settings no-ops.
    report_type: str
    report_structure_prompt: str

    # ── Search & Telemetry ────────────────────────────────────────────────
    search_results: Annotated[list[SearchResultItem], operator.add]
    search_executions: Annotated[list[SearchExecution], operator.add]
    # Raw-query results the planner prefetched concurrently with decomposition,
    # handed to the search node so it skips re-running that query.
    prefetched_query: str
    prefetched_results: Annotated[list[SearchResultItem], replace_list]
    # This round's results only; the ranker ranks these, not the history.
    round_search_results: Annotated[list[SearchResultItem], replace_list]
    # `replace_list`, NOT `operator.add`: the ranker runs after search and
    # emits the authoritative ranked URL set. With `operator.add` the raw
    # search list (~66 URLs) was concatenated *ahead* of the ranked list, and
    # scrape_node takes [:max_pages] — so it always scraped the unranked head
    # and the ranker's output was discarded. replace_list makes the ranker the
    # last writer, which is what the graph intends.
    urls_to_scrape: Annotated[list[str], replace_list]

    # ── Scraping ──────────────────────────────────────────────────────────
    # Every page fetched in any loop: [{"url", "title", "content"}]. Content
    # is blanked after extraction; the text lives on in ``passages``.
    scraped_contents: Annotated[list[dict[str, Any]], merge_by_url]
    # Pages fetched this round, with full text, for the extractor.
    round_documents: Annotated[list[dict[str, Any]], replace_list]
    # The run's evidence store: every page chunked into passages (see
    # orchestration/passages.py). Ground truth for citations and verification.
    passages: Annotated[list[Any], merge_passages]
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

    # ── Evidence quality ─────────────────────────────────────────────────
    # Computed by the evaluation node from the run's own artefacts: citation
    # coverage, hallucination rate, and the claim status mix.
    evaluation_stats: dict[str, Any]
    # The validated corpus — a bounded sample of the retrieved page text,
    # captured by the state pruner before it strips the raw blobs. The
    # evaluation node measures hallucination against this.
    evidence_corpus: str

    # ── Report ────────────────────────────────────────────────────────────
    report_sections: Annotated[list[str], operator.add]
    final_report: str
    target_pages: int
    report_theme: str
    citations: Annotated[list[str], replace_list]
    # Numbered source list the report's [n] markers resolve against.
    report_sources: list[dict[str, Any]]

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
