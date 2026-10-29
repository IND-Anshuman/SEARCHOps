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

import asyncio
import re
from typing import TYPE_CHECKING, Any

import structlog

from searchops.core.context.research import ExecutionBudget, ResearchPlan
from searchops.search.contracts import SearchQuery
from searchops.search.domain.models import SearchProfile

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState
    from searchops.search.contracts import SearchResultItem
    from searchops.search.orchestrator import SearchOrchestrator

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


async def _prefetch_search(
    orchestrator: SearchOrchestrator, query: str, max_results: int
) -> list[SearchResultItem]:
    """Run the raw query's search, stamping per-query ranks for the ranker's RRF.

    Best-effort: any failure returns an empty list so the search node simply
    runs the query itself as it always did.
    """
    search_fn = getattr(orchestrator, "search", None) or getattr(orchestrator, "aggregate_search", None)
    if search_fn is None:
        return []
    res: list[SearchResultItem] = await search_fn(
        SearchQuery(query=query, max_results=max_results, profile=SearchProfile.DEEP)
    )
    return [r.model_copy(update={"rank": i + 1}) for i, r in enumerate(res)]


async def planner_node(
    state: ResearchState,
    *,
    llm_router: LLMRouter,
    orchestrator: SearchOrchestrator | None = None,
) -> ResearchState:
    """Decompose the query on round one; pass gap follow-ups through afterwards.

    On round one the raw query's search is fired concurrently with the
    decomposition LLM call, so first-page results are already arriving when the
    sub-queries land — overlapping the planner's latency with search I/O.
    """
    query = state.get("query", "")
    max_sources = state.get("max_sources", 5)

    if state.get("loop_count", 0) > 0 and state.get("plan") is not None:
        pending = state.get("pending_queries", []) or []
        log.info("Planner: reflection round, using gap follow-up queries", pending=len(pending))
        return {"iteration": state.get("iteration", 0) + 1}

    log.info("Planner node executing", query=query)
    depth = str(state.get("depth") or "standard")
    prompt = _PLANNER_PROMPT.format(query=query, n=_QUERIES_BY_DEPTH.get(depth, "4-5"))
    # Cheap short-reasoning call: a fast model, and temperature 0.0 so the
    # decomposition is deterministic and cacheable across repeat runs.
    fast = llm_router.fast_model() if hasattr(llm_router, "fast_model") else None

    # Overlap the decomposition LLM call with the raw query's search.
    decompose_task = asyncio.ensure_future(llm_router.generate(prompt=prompt, temperature=0.0, model=fast))
    prefetch_task: Any = None
    if orchestrator is not None and query:
        prefetch_task = asyncio.ensure_future(_prefetch_search(orchestrator, query, max_sources))

    response = await decompose_task
    sub_queries = parse_sub_queries(response)
    if query and query.lower() not in {q.lower() for q in sub_queries}:
        # The question itself is always worth one search.
        sub_queries.insert(0, query)

    prefetched: list[SearchResultItem] = []
    if prefetch_task is not None:
        try:
            prefetched = await prefetch_task
        except Exception as exc:  # pragma: no cover - prefetch is best-effort
            log.warning("Planner prefetch search failed; search node will run it", error=str(exc))
            prefetched = []

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

    log.info("Planner generated ResearchPlan", sub_query_count=len(sub_queries), prefetched=len(prefetched))
    return {
        "iteration": 0,
        "plan": plan,
        "budget": budget,
        "pending_queries": sub_queries,
        # Hand the search node the already-fetched raw-query results so it skips
        # re-running that query. Empty when prefetch was unavailable or failed.
        "prefetched_query": query if prefetched else "",
        "prefetched_results": prefetched,
    }
