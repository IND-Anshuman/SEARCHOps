"""
Planner node: decomposes the user query into sub-queries and
populates the initial ResearchPlan & ExecutionBudget.

Only the first round calls the LLM. On a reflection round the gap analyst has
already turned the critic's gaps into concrete follow-up queries
(``pending_queries``); re-decomposing the original question here would throw
those away and re-search the first round's queries, which is what the loop
used to do.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from searchops.core.context.research import ExecutionBudget, ResearchPlan

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

_PLANNER_PROMPT = """
You are a research planning assistant. Break the query below into {n} precise
web-search queries that together cover it from different angles: definitions
and background, current state and key players, quantitative data, comparisons
or alternatives, and limitations or criticism — whichever apply.

Each query must be short (under 12 words), specific, and searchable on its own.
Return ONLY a numbered list, one query per line, no commentary.

Query: {query}
"""

_QUERIES_BY_DEPTH = {"shallow": "3", "standard": "4-5", "deep": "6-8"}
_MAX_SUB_QUERIES = 8


def parse_sub_queries(response: str) -> list[str]:
    """Parse a numbered/bulleted list into clean, de-duplicated queries."""
    out: list[str] = []
    for line in response.splitlines():
        q = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip().strip('"').strip()
        if not q or q.endswith(":") or len(q) < 4:
            continue
        if q.lower() not in {o.lower() for o in out}:
            out.append(q)
    return out[:_MAX_SUB_QUERIES]


async def planner_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Decompose the query on round one; pass gap follow-ups through afterwards."""
    query = state.get("query", "")
    max_sources = state.get("max_sources", 5)

    if state.get("loop_count", 0) > 0 and state.get("plan") is not None:
        pending = state.get("pending_queries", []) or []
        log.info("Planner: reflection round, using gap follow-up queries", pending=len(pending))
        return {"iteration": state.get("iteration", 0) + 1}

    log.info("Planner node executing", query=query)
    depth = str(state.get("depth") or "standard")
    prompt = _PLANNER_PROMPT.format(query=query, n=_QUERIES_BY_DEPTH.get(depth, "4-5"))
    response = await llm_router.generate(prompt=prompt, temperature=0.1)
    sub_queries = parse_sub_queries(response)
    if query and query.lower() not in {q.lower() for q in sub_queries}:
        # The question itself is always worth one search.
        sub_queries.insert(0, query)

    plan = ResearchPlan(
        primary_query=query,
        sub_queries=sub_queries,
        search_budget=max_sources,
        confidence=0.95 if len(sub_queries) > 1 else 0.5,
    )
    budget = ExecutionBudget(
        remaining_searches=len(sub_queries) + 1,
        remaining_scrapes=max_sources,
    )

    log.info("Planner generated ResearchPlan", sub_query_count=len(sub_queries))
    return {"iteration": 0, "plan": plan, "budget": budget, "pending_queries": sub_queries}
