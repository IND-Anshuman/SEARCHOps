"""
Search node: fans out across search providers, enforces budget, and collects SearchExecution metadata.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from searchops.core.context.research import SearchExecution
from searchops.core.exceptions.application import UseCaseError
from searchops.search.contracts import SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchProfile
from searchops.search.orchestrator import SearchOrchestrator, search_orchestrator

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


async def search_node(
    state: ResearchState,
    *,
    orchestrator: SearchOrchestrator | None = None,
    aggregator: Any | None = None,
) -> ResearchState:
    """Execute federated search across ResearchPlan sub-queries, recording SearchExecution telemetry."""
    query = state.get("query", "")
    plan = state.get("plan")
    budget = state.get("budget")
    scrape_plan = state.get("scrape_plan") or {}
    req_pages = (
        scrape_plan.get("max_pages", 15) if isinstance(scrape_plan, dict) else getattr(scrape_plan, "max_pages", 15)
    )
    max_sources = max(state.get("max_sources", 10), req_pages)

    # Search this round's queue (planner sub-queries on round one, gap
    # follow-ups afterwards), never a query an earlier round already ran.
    already = {q.lower() for q in state.get("searched_queries", []) or []}
    queued = state.get("pending_queries")
    if queued is None:
        queued = list(plan.sub_queries) if plan and plan.sub_queries else ([query] if query else [])
    target_queries = [q for q in queued if q and q.lower() not in already]

    # Enforce the search budget on the first round only; follow-up rounds are
    # bounded by the gap analyst's own cap.
    if budget and budget.remaining_searches > 0 and not already:
        target_queries = target_queries[: budget.remaining_searches]

    log.info("Search node executing", primary_query=query, total_queries=len(target_queries))

    if not target_queries:
        return {"search_results": [], "search_executions": [], "urls_to_scrape": [], "round_search_results": []}

    _orch = orchestrator or aggregator or search_orchestrator
    executions: list[SearchExecution] = []
    all_results: list[SearchResultItem] = []
    seen_urls: set[str] = set()
    urls: list[str] = []

    async def _execute_single_query(q: str) -> list[SearchResultItem]:
        start_time = time.perf_counter()
        try:
            # Dispatch to orchestrator using DEEP profile
            search_q = SearchQuery(query=q, max_results=max_sources, profile=SearchProfile.DEEP)
            search_fn = getattr(_orch, "search", None) or getattr(_orch, "aggregate_search", None)
            if search_fn is None:
                raise UseCaseError("No search method available on orchestrator")
            res: list[SearchResultItem] = await search_fn(search_q)
            # Stamp each result with its rank in THIS query's list, which is
            # what reciprocal rank fusion in the ranker needs.
            res = [r.model_copy(update={"rank": i + 1}) for i, r in enumerate(res)]
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            providers = sorted({str(getattr(r, "provider", "") or "") for r in res} - {""})
            executions.append(
                SearchExecution(
                    query=q,
                    provider=",".join(providers) or "none",
                    latency_ms=elapsed_ms,
                    cost_usd=_provider_cost(providers),
                    result_count=len(res),
                )
            )
            return res
        except Exception as exc:
            log.error("Single sub-query search failed", query=q, error=str(exc))
            return []

    tasks = [_execute_single_query(q) for q in target_queries]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in raw_results:
        if isinstance(res, list):
            for item in res:
                all_results.append(item)
                if item.url and item.url not in seen_urls:
                    seen_urls.add(item.url)
                    urls.append(item.url)

    log.info(
        "Search node complete",
        total_results=len(all_results),
        unique_urls=len(urls),
        executions=len(executions),
    )
    return {
        "search_results": all_results,
        "round_search_results": all_results,
        "search_executions": executions,
        "searched_queries": target_queries,
        "urls_to_scrape": urls,
    }


def _provider_cost(providers: list[str]) -> float:
    """Cost of one query from the providers' own declared per-query price."""
    from searchops.search.registry import registry

    total = 0.0
    for name in providers:
        provider = registry.get_provider(name)
        if provider is not None:
            try:
                total += float(provider.cost_per_query)
            except (TypeError, ValueError):
                continue
    return round(total, 6)
