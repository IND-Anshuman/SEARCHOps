"""
Planner node: decomposes the user query into sub-queries and
populates the initial ResearchPlan & ExecutionBudget.
"""

from __future__ import annotations

import structlog

from searchops.core.context.research import ExecutionBudget, ResearchPlan
from searchops.llm.router import LLMRouter
from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

_PLANNER_PROMPT = """
You are a research planning assistant. Break down the following query into
3-5 precise search sub-queries that collectively cover it from different angles.
Return ONLY a numbered list, one sub-query per line.

Query: {query}
"""


async def planner_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Decompose the top-level query into a structured ResearchPlan & ExecutionBudget."""
    query = state.get("query", "")
    max_sources = min(state.get("max_sources", 5), 10)
    log.info("Planner node executing", query=query)

    prompt = _PLANNER_PROMPT.format(query=query)
    response = await llm_router.generate(prompt=prompt, temperature=0.1)

    sub_queries = [
        line.lstrip("0123456789. ").strip()
        for line in response.splitlines()
        if line.strip()
    ]

    plan = ResearchPlan(
        primary_query=query,
        sub_queries=sub_queries,
        search_budget=max_sources,
        confidence=0.95 if sub_queries else 0.5,
    )
    budget = ExecutionBudget(
        remaining_searches=min(len(sub_queries) + 1, 10),
        remaining_scrapes=max_sources,
    )

    log.info("Planner generated ResearchPlan", sub_query_count=len(sub_queries))
    return {"iteration": 0, "plan": plan, "budget": budget}  # type: ignore[misc]


