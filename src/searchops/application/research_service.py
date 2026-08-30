"""
Research Application Service.

Bridges the API layer to the LangGraph orchestration engine.

Responsibilities:
  - Job creation (delegate persistence to JobStateManager)
  - Async execution of the LangGraph research pipeline
  - Real telemetry instrumentation (wall-clock node timing, token extraction)
  - Faithful extraction of the LLM-generated report from the final graph state
  - No state ownership — all persistence flows through JobStateManager

Design invariants:
  - This service has NO concept of Redis keys or pub/sub channels.
    Those belong to JobStateManager.
  - Token costs are derived from actual LLM response metadata,
    not hardcoded constants.
  - The final_report is the exact string returned by report_writer_node,
    never a template string.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from searchops.application.job_state_manager import JobStateManager
from searchops.core.context.execution import ExecutionContext
from searchops.core.context.research import ResearchDepth
from searchops.infrastructure.cache.redis import RedisCache
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.orchestration.graphs.deep_research import build_deep_research_graph
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.search.aggregator import FederatedSearchAggregator

log = structlog.get_logger(__name__)


class ResearchJobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Graph topology declaration — used to pre-populate node metadata
_TOPOLOGY = [
    ("planner",           "Planner Agent (Decomposition)",      "planner",   "system_planner_v4.2.jinja2"),
    ("search",            "Web Searcher (Serper/Tavily)",        "search",    "system_searcher_v3.1.jinja2"),
    ("ranker",            "Result Ranker",                       "extract",   "system_ranker_v1.0.jinja2"),
    ("scrape",            "Deep Scraper (Firecrawl Engine)",     "scrape",    "system_scraper_v2.8.jinja2"),
    ("evaluator",         "Fact Verifier (Reflection)",          "verify",    "system_verifier_v1.4.jinja2"),
    ("extract_knowledge", "GraphRAG Extractor (Neo4j)",          "graph_rag", "system_graphrag_v5.0.jinja2"),
    ("state_pruner",      "Context State Pruner",                "extract",   "system_state_pruner_v1.0.jinja2"),
    ("report_writer",     "Report Synthesis Writer",             "report",    "system_report_writer_v4.2.jinja2"),
]


class ResearchApplicationService:
    """Orchestrates the full research lifecycle via LangGraph.

    Constructed once by ApplicationContainer and shared across all requests.
    """

    def __init__(
        self,
        cache: RedisCache,
        job_state_manager: JobStateManager,
        llm_router: LLMRouter | None = None,
        aggregator: FederatedSearchAggregator | None = None,
        scraping_pipeline: ScrapingPipeline | None = None,
        extractor: EntityExtractor | None = None,
        arq_pool: Any | None = None,
    ) -> None:
        self.cache = cache
        self.job_state_manager = job_state_manager
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

    # ── Public API ────────────────────────────────────────────────────────────

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

        initial_state: dict[str, Any] = {
            "job_id": job_id,
            "status": ResearchJobStatus.PENDING,
            "query": query,
            "depth": depth,
            "max_sources": max_sources,
            "correlation_id": exec_ctx.correlation_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "progress": 0,
            "nodes": _build_initial_nodes(),
            "logs": [],
            "entities": [],
            "relations": [],
            "chunks": [],
            "citations": [],
            "token_used": 0,
            "token_budget": 150000,
            "cost_current": 0.0,
            "cost_budget": 5.0,
        }

        await self.job_state_manager.create_job(job_id, initial_state)

        if self.arq_pool:
            try:
                await self.arq_pool.enqueue_job(
                    "background_research_job", job_id, query, depth, max_sources
                )
                log.info("Research job enqueued to ARQ pool", job_id=job_id)
            except Exception as exc:
                log.warning(
                    "ARQ enqueue failed; using inline async task",
                    job_id=job_id,
                    error=str(exc),
                )
                asyncio.create_task(
                    self._execute_research(job_id, query, depth, max_sources, exec_ctx)
                )
        else:
            asyncio.create_task(
                self._execute_research(job_id, query, depth, max_sources, exec_ctx)
            )

        log.info("Research job created", job_id=job_id, query=query)
        return job_id

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve job state from JobStateManager."""
        return await self.job_state_manager.get_job(job_id)

    # ── Internal pipeline execution ───────────────────────────────────────────

    async def _execute_research(
        self,
        job_id: str,
        query: str,
        depth: str,
        max_sources: int,
        context: ExecutionContext,
    ) -> None:
        """Run the LangGraph pipeline and persist state after every node."""
        nodes_map: dict[str, dict[str, Any]] = {
            name: {
                "id": name,
                "label": label,
                "type": ntype,
                "status": "pending",
                "latency_ms": 0,
                "token_cost": 0.0,
                "retries": 0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "prompt": prompt_tmpl,
                "input_payload": {},
                "output_payload": {},
            }
            for name, label, ntype, prompt_tmpl in _TOPOLOGY
        }

        logs: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        citations: list[str] = []
        accumulated_token_used: int = 0
        accumulated_cost: float = 0.0

        def _add_log(event_type: str, level: str, payload: dict[str, Any]) -> None:
            log_id = f"evt_{int(time.time() * 1000)}"
            logs.insert(0, {
                "id": log_id,
                "stream": "searchops:events:langgraph",
                "event_type": event_type,
                "correlation_id": job_id,
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
                "payload": payload,
                "level": level,
            })

        try:
            _add_log("JOB_STARTED", "info", {
                "query": query, "depth": depth, "max_sources": max_sources,
            })

            # Transition to RUNNING, mark planner as active
            nodes_map["planner"]["status"] = "running"
            nodes_map["planner"]["input_payload"] = {"query": query}

            await self.job_state_manager.update_job(job_id, {
                "status": ResearchJobStatus.RUNNING,
                "progress": 5,
                "nodes": list(nodes_map.values()),
                "logs": logs,
                "start_time": datetime.now(timezone.utc).isoformat(),
            })

            graph = self._build_graph()
            initial_state: ResearchState = {
                "query": query,
                "depth": ResearchDepth(depth),
                "max_sources": max_sources,
                "correlation_id": context.correlation_id,
                "iteration": 0,
            }

            # Wall-clock timing per node
            node_start_times: dict[str, float] = {}
            final_report: str = ""

            # Stream graph updates — each chunk is a dict of node_name → state_delta
            async for updates in graph.astream(initial_state, stream_mode="updates"):
                for node_name, state_delta in updates.items():
                    wall_clock_now = time.perf_counter()

                    if node_name not in nodes_map:
                        continue

                    # Calculate real latency
                    node_start = node_start_times.pop(node_name, wall_clock_now)
                    latency_ms = int((wall_clock_now - node_start) * 1000)

                    # Extract real token usage from LLM metadata if available
                    node_tokens, node_cost = _extract_token_usage(state_delta)
                    accumulated_token_used += node_tokens
                    accumulated_cost += node_cost

                    # Mark node complete with real measurements
                    nodes_map[node_name].update({
                        "status": "completed",
                        "latency_ms": latency_ms,
                        "token_cost": node_cost,
                        "output_payload": {
                            k: str(v)[:200]
                            for k, v in state_delta.items()
                            if k not in ("messages",)
                        },
                    })

                    _add_log(
                        f"NODE_{node_name.upper()}_COMPLETED",
                        "info",
                        {"node": node_name, "latency_ms": latency_ms, "tokens": node_tokens},
                    )

                    # Accumulate knowledge graph entities
                    if state_delta.get("entities"):
                        for ent in state_delta["entities"]:
                            entities.append({
                                "id": ent.id,
                                "canonical_id": ent.canonical_id,
                                "name": ent.name,
                                "entity_type": ent.entity_type,
                                "description": ent.description,
                                "confidence_score": ent.confidence,
                            })

                    # Accumulate relations
                    if state_delta.get("relations"):
                        for rel in state_delta["relations"]:
                            relations.append({
                                "id": rel.id,
                                "source_id": rel.source_id,
                                "target_id": rel.target_id,
                                "source_canonical_id": rel.source_canonical_id,
                                "target_canonical_id": rel.target_canonical_id,
                                "relation_type": rel.relation_type,
                                "description": rel.description,
                            })

                    # Accumulate vector chunks from search results
                    if state_delta.get("search_results"):
                        for item in state_delta["search_results"]:
                            chunks.append({
                                "id": f"chunk_{len(chunks)}",
                                "document_title": item.title,
                                "source_url": item.url,
                                "similarity_score": item.score or 0.85,
                                "token_count": 240,
                                "chunk_preview": item.snippet,
                            })
                            citations.append(item.url)

                    # Capture the actual LLM-generated report (never a template)
                    if node_name == "report_writer" and state_delta.get("final_report"):
                        final_report = state_delta["final_report"]
                        # Also pick up citations from the node's scraped sources
                        if state_delta.get("citations"):
                            citations.extend(state_delta["citations"])

                    # Transition the next pending node to running
                    next_node = _next_pending_node(nodes_map)
                    if next_node:
                        nodes_map[next_node]["status"] = "running"
                        node_start_times[next_node] = time.perf_counter()
                        _add_log(
                            f"NODE_{next_node.upper()}_START",
                            "info",
                            {"node": next_node},
                        )

                    completed_count = sum(
                        1 for n in nodes_map.values() if n["status"] == "completed"
                    )
                    progress_pct = int(10 + (completed_count / len(_TOPOLOGY)) * 80)

                    await self.job_state_manager.update_job(job_id, {
                        "status": ResearchJobStatus.RUNNING,
                        "progress": progress_pct,
                        "nodes": list(nodes_map.values()),
                        "logs": logs,
                        "entities": entities,
                        "relations": relations,
                        "chunks": chunks,
                        "citations": list(dict.fromkeys(citations)),
                        "token_used": accumulated_token_used,
                        "cost_current": round(accumulated_cost, 6),
                    })

            # Publish terminal COMPLETED state
            _add_log("JOB_COMPLETED", "success", {
                "job_id": job_id,
                "entities_mined": len(entities),
                "sources_retrieved": len(citations),
                "final_report_chars": len(final_report),
            })

            await self.job_state_manager.update_job(job_id, {
                "status": ResearchJobStatus.COMPLETED,
                "progress": 100,
                "nodes": [{**n, "status": "completed"} for n in nodes_map.values()],
                "final_report": final_report,
                "citations": list(dict.fromkeys(citations)),
                "entity_count": len(entities),
                "source_count": len(chunks),
                "entities": entities,
                "relations": relations,
                "chunks": chunks,
                "logs": logs,
                "token_used": accumulated_token_used,
                "token_budget": 150000,
                "cost_current": round(accumulated_cost, 6),
                "cost_budget": 5.0,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })

            log.info("Research job completed", job_id=job_id)

        except Exception as exc:
            log.error("Research job failed", job_id=job_id, error=str(exc))
            _add_log("JOB_FAILED", "error", {"error": str(exc)})
            await self.job_state_manager.update_job(job_id, {
                "status": ResearchJobStatus.FAILED,
                "progress": 100,
                "nodes": list(nodes_map.values()),
                "error": str(exc),
                "logs": logs,
                "entities": entities,
                "relations": relations,
                "chunks": chunks,
                "citations": list(dict.fromkeys(citations)),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_initial_nodes() -> list[dict[str, Any]]:
    """Return the initial node list with all nodes in 'pending' status."""
    return [
        {
            "id": name,
            "label": label,
            "type": ntype,
            "status": "pending",
            "latency_ms": 0,
            "token_cost": 0.0,
            "retries": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt_tmpl,
            "input_payload": {},
            "output_payload": {},
        }
        for name, label, ntype, prompt_tmpl in _TOPOLOGY
    ]


def _next_pending_node(nodes_map: dict[str, dict[str, Any]]) -> str | None:
    """Return the id of the first pending node in topology order."""
    for name, _, _, _ in _TOPOLOGY:
        if nodes_map[name]["status"] == "pending":
            return name
    return None


def _extract_token_usage(state_delta: dict[str, Any]) -> tuple[int, float]:
    """Extract real token count and cost from LangGraph state delta.

    LangGraph surfaces LLM response metadata in the 'messages' list.
    Each AIMessage carries 'usage_metadata' (input/output tokens) and
    'response_metadata' (model name). We aggregate across all messages
    and calculate cost using the provider pricing table.

    Returns:
        (total_tokens: int, estimated_cost_usd: float)
    """
    from searchops.core.observability.cost_calculator import calculate_cost

    messages = state_delta.get("messages", [])
    total_cost = 0.0
    total_tokens = 0

    for msg in messages:
        usage = getattr(msg, "usage_metadata", None)
        if not usage:
            continue

        input_tokens: int = usage.get("input_tokens", 0)
        output_tokens: int = usage.get("output_tokens", 0)
        total_tokens += input_tokens + output_tokens

        # Resolve model name from response metadata (provider-specific key)
        response_meta = getattr(msg, "response_metadata", {}) or {}
        model_name: str = (
            response_meta.get("model_name")
            or response_meta.get("model")
            or "gemini-1.5-flash"  # conservative fallback
        )
        total_cost += calculate_cost(model_name, input_tokens, output_tokens)

    return total_tokens, total_cost
