import asyncio
import hashlib
import time
import random
import structlog
from typing import List, Set

from searchops.search.contracts import ISearchProvider, SearchQuery, SearchResultItem
from searchops.search.domain.models import SearchCapability
from searchops.search.registry import registry
from searchops.search.health import health_monitor
from searchops.search.cache import search_cache
from searchops.search.policy import budget_service, policy_engine
from searchops.search.aggregator import FederatedSearchAggregator
from searchops.config.settings import get_settings

log = structlog.get_logger(__name__)

class SearchOrchestrator:
    """Enterprise Search Orchestrator managing caching, routing, locks, retries, and fallbacks."""

    def __init__(self, aggregator: FederatedSearchAggregator | None = None) -> None:
        self.aggregator = aggregator or FederatedSearchAggregator()

    def _get_providers_hash(self, providers: List[ISearchProvider]) -> str:
        names = sorted([p.name for p in providers])
        return hashlib.md5("".join(names).encode("utf-8")).hexdigest()

    async def search(self, query: SearchQuery, job_id: str = "") -> List[SearchResultItem]:
        """Orchestrates search queries across caching, policy rules, and provider cascades."""
        from searchops.search.domain.models import SearchProfile

        # Resolve capabilities from profile
        required_caps = policy_engine.apply_profile(query)

        # Get active candidates matching capabilities
        candidates = registry.resolve_by_capabilities(required_caps)
        if not candidates:
            # Fallback to all enabled providers if no subset matches exactly
            candidates = registry.list_providers()

        # Routing is purely capability + priority based.
        # Provider classes declare `priority: int` as a class attribute.
        # No provider-name string matching here — open/closed principle.

        providers_hash = self._get_providers_hash(candidates)

        # 1. Double-Checked Cache Lookups
        cached = await search_cache.get(query.query, providers_hash)
        if cached:
            log.info("Search cache hit (exact)", query=query.query)
            return cached

        cached_sem = await search_cache.get_semantic(query.query, providers_hash, threshold=0.90)
        if cached_sem:
            log.info("Search cache hit (semantic)", query=query.query)
            return cached_sem

        # 2. Synchronize with Distributed Lock
        async with search_cache.lock_query(query.query) as acquired:
            if acquired:
                # Double-check cache inside lock context
                cached = await search_cache.get(query.query, providers_hash)
                if cached:
                    return cached

            # 3. Filter candidates by health (Circuit Breaker check)
            active_providers = [p for p in candidates if health_monitor.get_breaker(p.name).can_execute()]
            if not active_providers:
                log.warn("All search candidates are blocked by Circuit Breakers! Falling back to backup providers.")
                # Force list all providers (ignoring breaker temporarily for fallback survival)
                active_providers = registry.list_providers()

            # 4. Adaptive Parallelism Execution
            results_nested: List[List[SearchResultItem]] = []
            
            # Identify Tavily and Serper for potential parallel execution
            tavily_p = next((p for p in active_providers if p.name == "tavily"), None)
            serper_p = next((p for p in active_providers if p.name == "serper"), None)
            
            # Simple adaptive policy: if budget is healthy and both are active, query both in parallel
            if tavily_p and serper_p and budget_service.is_within_budget(job_id, tavily_p.cost_per_query + serper_p.cost_per_query):
                log.info("Adaptive parallelism: Executing Tavily and Serper in parallel", query=query.query)
                t1 = self._execute_with_retry(tavily_p, query, job_id)
                t2 = self._execute_with_retry(serper_p, query, job_id)
                res_t, res_s = await asyncio.gather(t1, t2)
                results_nested.append(res_t)
                results_nested.append(res_s)
            else:
                # Sequential Priority Routing
                success = False
                for provider in active_providers:
                    if not budget_service.is_within_budget(job_id, provider.cost_per_query):
                        continue
                    res = await self._execute_with_retry(provider, query, job_id)
                    if res:
                        results_nested.append(res)
                        success = True
                        break # First priority provider succeeded, stop sequential cascade
                
                # If TIER 1 preferred fails, fallback to TIER 2 (SearXNG) and TIER 3 (Playwright)
                if not success:
                    fallback_providers = [p for p in registry.list_providers() if p.name in ("searxng", "playwright")]
                    for provider in fallback_providers:
                        log.info("Triggering search provider fallback", failed_query=query.query, fallback_provider=provider.name)
                        res = await self._execute_with_retry(provider, query, job_id)
                        if res:
                            results_nested.append(res)
                            break

            # 5. Fuse & Aggregate Results
            fused = self.aggregator.fuse_results(results_nested)
            
            # Save original query context inside metadata of first result for Jaccard semantic cache key retrieval
            if fused:
                fused[0].raw_metadata["original_query"] = query.query
                # ── PAA sub-query passthrough (Premium Deep Search) ────────
                # PAA subqueries are stored ONLY on fused[0] to avoid token
                # duplication. Broadcasting to all items was duplicating the same
                # list across N result items, inflating context by N×.
                from searchops.search.domain.models import SearchProfile
                if query.profile in (SearchProfile.PREMIUM, SearchProfile.DEEP):
                    paa: list[str] = fused[0].raw_metadata.get("paa_subqueries", [])
                    if paa:
                        # Depth guard: prevent unbounded PAA expansion loops
                        paa_depth = int(query.raw_metadata.get("paa_depth", 0)) if query.raw_metadata else 0
                        max_paa_depth = get_settings().search.max_paa_depth
                        max_paa_per_serp = get_settings().search.max_paa_per_serp
                        if paa_depth >= max_paa_depth:
                            log.warning(
                                "PAA depth guard: max depth reached, suppressing PAA expansion",
                                paa_depth=paa_depth,
                                max_paa_depth=max_paa_depth,
                                query=query.query,
                            )
                            from searchops.scraping.bd_metrics import BD_PAA_DEPTH_REJECTIONS
                            BD_PAA_DEPTH_REJECTIONS.inc()
                            # Clear PAA from fused[0] so callers don't enqueue sub-queries
                            fused[0].raw_metadata.pop("paa_subqueries", None)
                        else:
                            # Deduplicate and truncate PAA subqueries
                            seen: set[str] = set()
                            deduped_paa: list[str] = []
                            for q in paa:
                                if q not in seen:
                                    seen.add(q)
                                    deduped_paa.append(q)
                                    if len(deduped_paa) >= max_paa_per_serp:
                                        break
                            # Store deduplicated copy on fused[0] only
                            fused[0].raw_metadata["paa_subqueries"] = deduped_paa
                            from searchops.scraping.bd_metrics import BD_PAA_SUBQUERIES
                            BD_PAA_SUBQUERIES.inc(len(deduped_paa))
                            log.info(
                                "PAA sub-queries stored on fused[0] (not broadcast)",
                                count=len(deduped_paa),
                                depth=paa_depth,
                                query=query.query,
                            )

                ttl_sec = getattr(query, "cache_ttl", get_settings().search.cache_ttl)
                await search_cache.set(query.query, providers_hash, fused, ttl_sec=ttl_sec)

            return fused[:query.max_results]

    async def _execute_with_retry(self, provider: ISearchProvider, query: SearchQuery, job_id: str) -> List[SearchResultItem]:
        """Execute a search provider query with exponential backoff and jitter."""
        max_attempts = 3
        backoff_sec = 0.5
        
        for attempt in range(1, max_attempts + 1):
            start_time = time.perf_counter()
            try:
                # Wrap search task with timeout
                res = await asyncio.wait_for(provider.search(query), timeout=5.0)
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                
                # Record telemetry metrics
                health_monitor.record_query(provider.name, elapsed_ms, success=True)
                budget_service.record_cost(job_id, provider.cost_per_query)
                return res
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                log.warn("Search provider query failed", provider=provider.name, attempt=attempt, error=str(e))
                
                if attempt == max_attempts:
                    health_monitor.record_query(provider.name, elapsed_ms, success=False)
                    break
                
                # Sleep with exponential backoff and random jitter
                sleep_time = backoff_sec * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
                await asyncio.sleep(sleep_time)

        return []


# Global search orchestrator instance
search_orchestrator = SearchOrchestrator()
