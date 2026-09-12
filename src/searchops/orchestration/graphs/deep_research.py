"""
Deep Research LangGraph Compiled State Graph.

Pipeline (post Wave-2 wiring):

  START → planner → search → ranker → scrape → extract_claims →
         critic → verifier ──[gaps? & under loop cap]──→ gap_analyst → planner
                          └─[no gaps | loop cap reached]─→ state_pruner → report_writer → END

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
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from searchops.core.context.research import ResearchDepth
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.orchestration.nodes.critic import critic_node
from searchops.orchestration.nodes.extract_claims import extract_claims_node
from searchops.orchestration.nodes.gap_analyst import gap_analyst_node
from searchops.orchestration.nodes.planner import planner_node
from searchops.orchestration.nodes.ranker import ranker_node
from searchops.orchestration.nodes.report_writer import report_writer_node
from searchops.orchestration.nodes.scrape import scrape_node
from searchops.orchestration.nodes.search import search_node
from searchops.orchestration.nodes.state_pruner import state_pruner_node
from searchops.orchestration.nodes.verifier import verifier_node
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.search.orchestrator import SearchOrchestrator

log = structlog.get_logger(__name__)

# Loop cap keyed on depth. Prevents unbounded reflection on adversarial queries.
_MAX_LOOPS = {
    ResearchDepth.SHALLOW: 1,
    ResearchDepth.STANDARD: 2,
    ResearchDepth.DEEP: 4,
}


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


def build_deep_research_graph(
    llm_router: LLMRouter | None = None,
    orchestrator: SearchOrchestrator | None = None,
    scraping_pipeline: ScrapingPipeline | None = None,
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
    _pipe = scraping_pipeline or ScrapingPipeline()

    graph = StateGraph(ResearchState)

    # Register nodes with injected dependencies via functools.partial
    graph.add_node("planner", functools.partial(planner_node, llm_router=_llm))
    graph.add_node("search", functools.partial(search_node, orchestrator=_orch))
    graph.add_node("ranker", ranker_node)
    graph.add_node("scrape", functools.partial(scrape_node, pipeline=_pipe))
    graph.add_node("extract_claims", functools.partial(extract_claims_node, llm_router=_llm))
    graph.add_node("critic", functools.partial(critic_node, llm_router=_llm))
    graph.add_node("verifier", functools.partial(verifier_node, llm_router=_llm))
    graph.add_node("gap_analyst", gap_analyst_node)
    graph.add_node("state_pruner", state_pruner_node)
    graph.add_node("report_writer", functools.partial(report_writer_node, llm_router=_llm))

    # Wire graph with the 3-arm reflection loop.
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "search")
    graph.add_edge("search", "ranker")
    graph.add_edge("ranker", "scrape")
    graph.add_edge("scrape", "extract_claims")
    graph.add_edge("extract_claims", "critic")
    graph.add_edge("critic", "verifier")
    graph.add_conditional_edges(
        "verifier",
        should_continue,
        {
            "gap_analyst": "gap_analyst",
            "state_pruner": "state_pruner",
        },
    )
    graph.add_edge("gap_analyst", "planner")
    graph.add_edge("state_pruner", "report_writer")
    graph.add_edge("report_writer", END)

    return graph.compile()