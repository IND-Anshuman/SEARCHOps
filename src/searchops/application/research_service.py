"""
Research Application Service.

Bridges the API layer to the LangGraph orchestration engine.
Owns: job creation, status tracking (Redis), result persistence, streaming progress events.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import structlog

from searchops.core.context.execution import ExecutionContext
from searchops.core.context.research import ResearchDepth
from searchops.core.exceptions.infrastructure import CacheError
from searchops.infrastructure.cache.redis import RedisCache
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.orchestration.graphs.deep_research import build_deep_research_graph
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.search.aggregator import FederatedSearchAggregator

log = structlog.get_logger(__name__)

_JOB_TTL = 3600  # 1 hour


class ResearchJobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchApplicationService:
    """Orchestrates the full research lifecycle via LangGraph."""

    def __init__(
        self,
        cache: RedisCache | None = None,
        llm_router: LLMRouter | None = None,
        aggregator: FederatedSearchAggregator | None = None,
        scraping_pipeline: ScrapingPipeline | None = None,
        extractor: EntityExtractor | None = None,
        arq_pool: Any | None = None,
    ) -> None:
        self.cache = cache
        self.llm_router = llm_router or LLMRouter(cache=cache)
        self.aggregator = aggregator or FederatedSearchAggregator()
        self.scraping_pipeline = scraping_pipeline or ScrapingPipeline(cache=cache)
        self.extractor = extractor or EntityExtractor(self.llm_router)
        self.arq_pool = arq_pool

    def _build_graph(self) -> Any:
        return build_deep_research_graph(
            llm_router=self.llm_router,
            aggregator=self.aggregator,
            scraping_pipeline=self.scraping_pipeline,
            extractor=self.extractor,
        )

    async def _set_status(self, job_id: str, status: dict[str, Any]) -> None:
        if self.cache:
            await self.cache.set(f"research:job:{job_id}", status, ttl_seconds=_JOB_TTL)

    async def _get_status(self, job_id: str) -> dict[str, Any] | None:
        if self.cache:
            return await self.cache.get(f"research:job:{job_id}")
        return None

    async def start_research(
        self,
        query: str,
        depth: str = "standard",
        max_sources: int = 10,
        context: ExecutionContext | None = None,
    ) -> str:
        """Create a research job and schedule async execution. Returns job_id."""
        job_id = str(uuid.uuid4())
        exec_ctx = context or ExecutionContext.create()

        initial_status: dict[str, Any] = {
            "job_id": job_id,
            "status": ResearchJobStatus.PENDING,
            "query": query,
            "depth": depth,
            "max_sources": max_sources,
            "correlation_id": exec_ctx.correlation_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "progress": 0,
        }
        await self._set_status(job_id, initial_status)

        # Enqueue job to Redis ARQ worker pool if available, otherwise inline fallback
        if self.arq_pool:
            try:
                await self.arq_pool.enqueue_job("background_research_job", job_id, query, depth, max_sources)
                log.info("Research job enqueued to ARQ Redis worker pool", job_id=job_id)
            except Exception as exc:
                log.warning("Failed to enqueue to ARQ pool, using inline task fallback", error=str(exc))
                asyncio.create_task(self._execute_research(job_id, query, depth, max_sources, exec_ctx))
        else:
            asyncio.create_task(self._execute_research(job_id, query, depth, max_sources, exec_ctx))

        log.info("Research job created", job_id=job_id, query=query)
        return job_id

    async def _execute_research(
        self,
        job_id: str,
        query: str,
        depth: str,
        max_sources: int,
        context: ExecutionContext,
    ) -> None:
        """Internal method: run the LangGraph pipeline and persist the result."""
        try:
            await self._set_status(job_id, {
                "job_id": job_id,
                "status": ResearchJobStatus.RUNNING,
                "query": query,
                "progress": 10,
            })

            graph = self._build_graph()
            initial_state: ResearchState = {
                "query": query,
                "depth": ResearchDepth(depth),
                "max_sources": max_sources,
                "correlation_id": context.correlation_id,
                "iteration": 0,
            }

            final_state: ResearchState = await graph.ainvoke(initial_state)

            await self._set_status(job_id, {
                "job_id": job_id,
                "status": ResearchJobStatus.COMPLETED,
                "query": query,
                "progress": 100,
                "final_report": final_state.get("final_report", ""),
                "citations": final_state.get("citations", []),
                "entity_count": len(final_state.get("entities", [])),
                "source_count": len(final_state.get("scraped_contents", [])),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            log.info("Research job completed", job_id=job_id)

        except Exception as exc:
            log.error("Research job failed", job_id=job_id, error=str(exc))
            await self._set_status(job_id, {
                "job_id": job_id,
                "status": ResearchJobStatus.FAILED,
                "query": query,
                "error": str(exc),
            })

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve job status from cache."""
        return await self._get_status(job_id)

    async def stream_progress(self, job_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """Yield status updates for SSE/WebSocket streaming."""
        max_polls = 120  # 2 minutes max
        for _ in range(max_polls):
            status = await self._get_status(job_id)
            if status:
                yield status
                if status.get("status") in (ResearchJobStatus.COMPLETED, ResearchJobStatus.FAILED):
                    break
            await asyncio.sleep(1.0)
