"""
Deep Research LangGraph Compiled State Graph.

Pipeline (post Wave-2 wiring):

  START → planner → search → ranker → scrape → extract_evidence →
         critic → verifier
                          ──[gaps? & under loop cap]──→ gap_analyst ─[new queries]→ planner
                          │                                         └─[none]→ state_pruner
                          └─[no gaps | loop cap reached]─→ state_pruner → report_writer → evaluation → END

Reflection loop semantics:
  - ``critic`` grades accumulated evidence; emits ``evidence_gaps``.
  - ``should_continue`` routes by three signals: remaining gaps, remaining
    budget, and ``loop_count`` against ``MAX_LOOPS``.
  - ``gap_analyst`` converts gaps into follow-up sub-queries and re-invokes the
    planner; the planner now sees gaps + history so it does NOT duplicate work.
  - ``state_pruner`` runs ONLY on the terminal transition (no more raw-blob
    trimming mid-loop) so evidence survives the loop.

All nodes are thin dispatchers; services are injected via functools.partial
so the graph remains pure and testable.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import structlog
from langgraph.graph import END, START, StateGraph

from searchops.core.context.research import ResearchDepth
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.orchestration.nodes.critic import _SYSTEM_CRITIC_PROMPT, critic_node
from searchops.orchestration.nodes.evaluation import evaluation_node
from searchops.orchestration.nodes.extract_evidence import _SYSTEM_UNIFIED_EXTRACTION_PROMPT, extract_evidence_node
from searchops.orchestration.nodes.gap_analyst import gap_analyst_node
from searchops.orchestration.nodes.planner import _PLANNER_PROMPT, planner_node
from searchops.orchestration.nodes.ranker import ranker_node
from searchops.orchestration.nodes.report_writer import report_writer_node
from searchops.orchestration.nodes.scrape import scrape_node
from searchops.orchestration.nodes.search import search_node
from searchops.orchestration.nodes.state_pruner import state_pruner_node
from searchops.orchestration.nodes.verifier import _SYSTEM_VERIFY_PROMPT, verifier_node
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.factory import build_scraping_pipeline
from searchops.search.orchestrator import SearchOrchestrator

if TYPE_CHECKING:
    from searchops.scraping.factory import PageFetcher

log = structlog.get_logger(__name__)

# Loop cap keyed on depth. Prevents unbounded reflection on adversarial queries.
_MAX_LOOPS = {
    ResearchDepth.SHALLOW: 1,
    ResearchDepth.STANDARD: 2,
    ResearchDepth.DEEP: 4,
}

# ── Single source of truth for node display metadata ──────────────────────────
#
# (node_id, label, frontend_node_type, prompt_template).
#
# `node_id` MUST match the id passed to graph.add_node() below — the API layer
# keys its streamed node deltas by these ids and silently drops any that do not
# match. `frontend_node_type` must be a member of the LangGraphNode.type union
# in frontend/src/shared/types/workbench.ts, or the node renders untyped.
#
# Declared here, next to the graph itself, so the two cannot drift.
# test_topology_contract.py enforces the match.
GRAPH_TOPOLOGY: list[tuple[str, str, str, str]] = [
    ("planner", "Planner (Query Decomposition)", "planner", _PLANNER_PROMPT.strip()),
    ("search", "Web Search (Federated)", "search", "No LLM call. Searches only queries not run in an earlier round."),
    ("ranker", "Result Ranker (RRF)", "extract", "No LLM call. Reciprocal rank fusion across sub-query result lists."),
    ("scrape", "Page Fetcher (Tiered Scraper)", "scrape", "No LLM call. Concurrent fetch of ranked URLs not yet read."),
    ("extract_evidence", "Evidence Extractor (Grounded Claims)", "extract", _SYSTEM_UNIFIED_EXTRACTION_PROMPT),
    ("critic", "Coverage Critic (Reflection)", "verify", _SYSTEM_CRITIC_PROMPT),
    ("verifier", "Source Verifier (Cross-source)", "verify", _SYSTEM_VERIFY_PROMPT),
    (
        "gap_analyst",
        "Gap Analyst (Follow-up Queries)",
        "extract",
        "No LLM call. Turns coverage gaps into new search queries.",
    ),
    ("state_pruner", "Context State Pruner", "extract", "No LLM call. Captures the passage corpus for evaluation."),
    (
        "report_writer",
        "Report Writer (Cited Synthesis)",
        "report",
        "Plans sections, then writes each from its most relevant verified claims.",
    ),
    (
        "evaluation",
        "Evidence Quality Evaluator",
        "verify",
        "No LLM call. Measures grounding, citation coverage and hallucination.",
    ),
]

# Ordered node ids, derived from the metadata above rather than restated.
GRAPH_NODE_ORDER: list[str] = [entry[0] for entry in GRAPH_TOPOLOGY]


def should_continue(state: ResearchState) -> str:
    """3-arm loop router: re-plan on a budget / gaps / cap, else terminate."""
    depth = state.get("depth") or ResearchDepth.STANDARD
    loop_count = state.get("loop_count", 0)
    gaps = state.get("evidence_gaps", [])

    if loop_count >= _MAX_LOOPS.get(depth, 2):
        log.info("Max research loops reached; proceeding to synthesis", loop_count=loop_count)
        return "state_pruner"

    open_gaps = [g for g in gaps if g.priority >= 1]
    if open_gaps:
        log.info("Research gaps remain; re-planning", gap_count=len(open_gaps), loop_count=loop_count)
        return "gap_analyst"

    log.info("Evidence judged sufficient; proceeding to synthesis", loop_count=loop_count)
    return "state_pruner"


def after_gap_analysis(state: ResearchState) -> str:
    """Loop only when the gap analyst queued queries that were never searched.

    Without new queries another round would re-run search over nothing and
    pay for a critic + verifier pass that cannot change the outcome.
    """
    if state.get("pending_queries"):
        return "planner"
    log.info("No new follow-up queries; proceeding to synthesis")
    return "state_pruner"


def build_deep_research_graph(
    llm_router: LLMRouter | None = None,
    orchestrator: SearchOrchestrator | None = None,
    scraping_pipeline: PageFetcher | None = None,
    extractor: EntityExtractor | None = None,
    aggregator: Any | None = None,
) -> Any:
    """Build and compile the Autonomous Deep Research LangGraph state machine.

    All dependencies are optional so callers can inject mocks during testing.
    Returns:
        Compiled LangGraph CompiledStateGraph ready for `.ainvoke()`.
    """
    _llm = llm_router or LLMRouter()
    _orch = orchestrator or aggregator or SearchOrchestrator()
    _pipe = scraping_pipeline or build_scraping_pipeline()
    # EntityExtractor only needs an LLM router, so it can always be built from
    # the router the graph already resolved.
    _extractor = extractor or EntityExtractor(_llm)

    graph = StateGraph(ResearchState)

    # Register nodes with injected dependencies via functools.partial
    graph.add_node("planner", functools.partial(planner_node, llm_router=_llm))
    graph.add_node("search", functools.partial(search_node, orchestrator=_orch))
    graph.add_node("ranker", ranker_node)
    graph.add_node("scrape", functools.partial(scrape_node, pipeline=_pipe))
    graph.add_node("extract_evidence", functools.partial(extract_evidence_node, llm_router=_llm))
    graph.add_node("critic", functools.partial(critic_node, llm_router=_llm))
    graph.add_node("verifier", functools.partial(verifier_node, llm_router=_llm))
    graph.add_node("gap_analyst", gap_analyst_node)
    graph.add_node("state_pruner", state_pruner_node)
    graph.add_node("report_writer", functools.partial(report_writer_node, llm_router=_llm))
    graph.add_node("evaluation", evaluation_node)

    # Wire graph with the 3-arm reflection loop.
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "search")
    graph.add_edge("search", "ranker")
    graph.add_edge("ranker", "scrape")
    graph.add_edge("scrape", "extract_evidence")
    graph.add_edge("extract_evidence", "critic")
    graph.add_edge("critic", "verifier")
    graph.add_conditional_edges(
        "verifier",
        should_continue,
        {
            "gap_analyst": "gap_analyst",
            "state_pruner": "state_pruner",
        },
    )
    graph.add_conditional_edges(
        "gap_analyst",
        after_gap_analysis,
        {"planner": "planner", "state_pruner": "state_pruner"},
    )
    graph.add_edge("state_pruner", "report_writer")
    graph.add_edge("report_writer", "evaluation")
    graph.add_edge("evaluation", END)

    # Fail fast at build time if the declared metadata and the actually
    # registered nodes diverge. Without this the API silently drops deltas for
    # unlisted nodes at runtime (the original topology-drift bug).
    registered = set(graph.nodes)
    declared = {entry[0] for entry in GRAPH_TOPOLOGY}
    if registered != declared:
        raise RuntimeError(
            "GRAPH_TOPOLOGY and the registered graph nodes disagree — "
            f"registered but undeclared: {sorted(registered - declared)}; "
            f"declared but unregistered: {sorted(declared - registered)}"
        )

    return graph.compile()
