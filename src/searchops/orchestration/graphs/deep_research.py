"""
Deep Research LangGraph Compiled State Graph.

Pipeline:
  START → planner → search → ranker → scrape → evaluator → extract_knowledge → state_pruner → report_writer → END

All nodes are thin dispatchers; services are injected via functools.partial
so the graph remains pure and testable.
"""

from __future__ import annotations

import functools
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.orchestration.nodes.extract_knowledge import extract_knowledge_node
from searchops.orchestration.nodes.planner import planner_node
from searchops.orchestration.nodes.ranker import ranker_node
from searchops.orchestration.nodes.report_writer import report_writer_node
from searchops.orchestration.nodes.scrape import scrape_node
from searchops.orchestration.nodes.search import search_node
from searchops.orchestration.nodes.state_pruner import state_pruner_node
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.search.orchestrator import SearchOrchestrator

log = structlog.get_logger(__name__)


def evaluator_node(state: ResearchState) -> ResearchState:
    """Evaluate research coverage and increment iteration counter."""
    iteration = state.get("iteration", 0) + 1
    return {"iteration": iteration}  # type: ignore[misc]


def should_continue(state: ResearchState) -> str:
    """Determine whether to loop back for query refinement or proceed to state pruning."""
    scraped = state.get("scraped_contents", [])
    iteration = state.get("iteration", 0)

    if len(scraped) == 0 and iteration < 2:
        log.info("Insufficient research coverage; looping back to planner", iteration=iteration)
        return "planner"

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
    graph.add_node("evaluator", evaluator_node)
    graph.add_node("state_pruner", state_pruner_node)
    graph.add_node(
        "report_writer", functools.partial(report_writer_node, llm_router=_llm)
    )

    # Wire graph with conditional reflection loop and state compaction
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "search")
    graph.add_edge("search", "ranker")
    graph.add_edge("ranker", "scrape")
    graph.add_edge("scrape", "evaluator")
    graph.add_conditional_edges(
        "evaluator",
        should_continue,
        {
            "planner": "planner",
            "state_pruner": "state_pruner",
        },
    )
    graph.add_edge("state_pruner", "report_writer")
    graph.add_edge("report_writer", END)

    compiled = graph.compile()
    log.info("Autonomous Deep Research graph compiled successfully with ranker & state pruner")
    return compiled

