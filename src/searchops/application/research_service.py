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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from searchops.application.report_document import ReportDocument
from searchops.core.context.execution import ExecutionContext
from searchops.core.context.research import ResearchConfig, ResearchDepth, ScrapePlan
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.orchestration.graphs.deep_research import (
    GRAPH_TOPOLOGY,
    build_deep_research_graph,
)
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.search.aggregator import FederatedSearchAggregator

if TYPE_CHECKING:
    from searchops.application.job_state_manager import JobStateManager
    from searchops.infrastructure.cache.redis import RedisCache
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


class ResearchJobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Node display metadata is owned by the graph itself so the two cannot drift.
# _execute_research() keys streamed deltas by these ids and silently drops
# unmatched ones, so importing (not copying) the list is load-bearing.
_TOPOLOGY = GRAPH_TOPOLOGY


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
        from searchops.llm.cache import LLMResponseCache

        llm_cache = LLMResponseCache(cache_backend=cache) if cache else None
        self.llm_router = llm_router or LLMRouter(cache=llm_cache)
        self.aggregator = aggregator or FederatedSearchAggregator()
        self.scraping_pipeline = scraping_pipeline or ScrapingPipeline(cache=cache)
        self.extractor = extractor or EntityExtractor(self.llm_router)
        self.arq_pool = arq_pool
        # Strong refs to background tasks so the event loop doesn't GC them mid-flight
        self._bg_tasks: set[asyncio.Task[None]] = set()

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
        mode: str = "precision",
        config: ResearchConfig | None = None,
        context: ExecutionContext | None = None,
    ) -> str:
        """Create a research job and schedule async execution. Returns job_id.

        ``mode`` selects the run shape (precision | corpus). Its resolved
        :class:`ScrapePlan` is stored on the job so every downstream stage can
        read its own parameters instead of re-deriving the mode.

        ``config`` is the user's tuning. When supplied it overrides the plan's
        breadth and depth; the plan's other fields (relevance filter,
        verification sampling, report shape) stay mode-owned because they are
        structural choices, not knobs a user should have to reason about.
        """
        job_id = str(uuid.uuid4())
        exec_ctx = context or ExecutionContext.create()

        # An invalid mode is rejected here rather than silently coerced — a
        # job that quietly ran as precision when corpus was asked for is worse
        # than a clear 422.
        plan = ScrapePlan.for_mode(mode, max_sources=max_sources)
        if config is not None:
            plan = plan.model_copy(
                update={
                    "max_pages": config.max_pages,
                    "max_depth": config.max_depth,
                    "verify_sampling_rate": config.verify_sampling_rate,
                }
            )

        initial_state: dict[str, Any] = {
            "job_id": job_id,
            "status": ResearchJobStatus.PENDING,
            "query": query,
            "depth": depth,
            "mode": plan.mode.value,
            "scrape_plan": plan.model_dump(mode="json"),
            "max_sources": max_sources,
            "correlation_id": exec_ctx.correlation_id,
            "created_at": datetime.now(UTC).isoformat(),
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
                await self.arq_pool.enqueue_job("background_research_job", job_id, query, depth, max_sources)
                log.info("Research job enqueued to ARQ pool", job_id=job_id)
            except Exception as exc:
                log.warning(
                    "ARQ enqueue failed; using inline async task",
                    job_id=job_id,
                    error=str(exc),
                )
                task = asyncio.create_task(self._execute_research(job_id, query, depth, max_sources, exec_ctx))
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
        else:
            task = asyncio.create_task(self._execute_research(job_id, query, depth, max_sources, exec_ctx))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
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
                "timestamp": datetime.now(UTC).isoformat(),
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
            logs.insert(
                0,
                {
                    "id": log_id,
                    "stream": "searchops:events:langgraph",
                    "event_type": event_type,
                    "correlation_id": job_id,
                    "timestamp": datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3],
                    "payload": payload,
                    "level": level,
                },
            )

        try:
            _add_log(
                "JOB_STARTED",
                "info",
                {
                    "query": query,
                    "depth": depth,
                    "max_sources": max_sources,
                },
            )

            # Transition to RUNNING, mark planner as active
            nodes_map["planner"]["status"] = "running"
            nodes_map["planner"]["input_payload"] = {"query": query}

            await self.job_state_manager.update_job(
                job_id,
                {
                    "status": ResearchJobStatus.RUNNING,
                    "progress": 5,
                    "nodes": list(nodes_map.values()),
                    "logs": logs,
                    "start_time": datetime.now(UTC).isoformat(),
                },
            )

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
            # Provenance for how the report came to look the way it does.
            # report_writer_node returns these alongside final_report; they must
            # reach the client so the UI can explain a degraded report honestly
            # instead of showing it as if the model had written it.
            report_quality: str = ""
            report_user_message: str = ""
            # Structured evidence, surfaced so the UI can show what the run
            # could NOT establish rather than only what it did.
            evidence_gaps: list[Any] = []
            evidence_claims: list[Any] = []
            # Canonical block model for the report. Built once, at the end,
            # from the evidence the run actually produced — so every
            # block-level feature (export, where-to-go-next, study guide,
            # trust header) reads the same addressable structure instead of
            # re-parsing Markdown its own way.
            report_document: dict[str, Any] = {}

            # Stream graph updates — each chunk is a dict of node_name → state_delta
            async for updates in graph.astream(initial_state, stream_mode="updates"):
                for node_name, state_delta in updates.items():
                    wall_clock_now = time.perf_counter()

                    if node_name not in nodes_map:
                        continue

                    # A node that returns {} is surfaced by LangGraph as a None
                    # delta. Normalise it so every downstream read is safe.
                    if state_delta is None:
                        state_delta = {}

                    # Calculate real latency
                    node_start = node_start_times.pop(node_name, wall_clock_now)
                    latency_ms = int((wall_clock_now - node_start) * 1000)

                    # Extract real token usage from LLM metadata if available
                    node_tokens, node_cost = _extract_token_usage(state_delta)
                    accumulated_token_used += node_tokens
                    accumulated_cost += node_cost

                    # Mark node complete with real measurements
                    nodes_map[node_name].update(
                        {
                            "status": "completed",
                            "latency_ms": latency_ms,
                            "token_cost": node_cost,
                            "output_payload": {
                                k: str(v)[:200] for k, v in state_delta.items() if k not in ("messages",)
                            },
                        }
                    )

                    _add_log(
                        f"NODE_{node_name.upper()}_COMPLETED",
                        "info",
                        {"node": node_name, "latency_ms": latency_ms, "tokens": node_tokens},
                    )

                    # Accumulate knowledge graph entities
                    if state_delta.get("entities"):
                        for ent in state_delta["entities"]:
                            entities.append(
                                {
                                    "id": ent.id,
                                    "canonical_id": ent.canonical_id,
                                    "name": ent.name,
                                    "entity_type": ent.entity_type,
                                    "description": ent.description,
                                    "confidence_score": ent.confidence,
                                }
                            )

                    # Accumulate relations
                    if state_delta.get("relations"):
                        for rel in state_delta["relations"]:
                            relations.append(
                                {
                                    "id": rel.id,
                                    "source_id": rel.source_id,
                                    "target_id": rel.target_id,
                                    "source_canonical_id": rel.source_canonical_id,
                                    "target_canonical_id": rel.target_canonical_id,
                                    "relation_type": rel.relation_type,
                                    "description": rel.description,
                                }
                            )

                    # Accumulate vector chunks from search results
                    if state_delta.get("search_results"):
                        for item in state_delta["search_results"]:
                            chunks.append(
                                {
                                    "id": f"chunk_{len(chunks)}",
                                    "document_title": item.title,
                                    "source_url": item.url,
                                    "similarity_score": item.score or 0.85,
                                    "token_count": 240,
                                    "chunk_preview": item.snippet,
                                }
                            )
                            citations.append(item.url)

                    # Capture the actual LLM-generated report (never a template)
                    if node_name == "report_writer" and state_delta.get("final_report"):
                        final_report = state_delta["final_report"]
                        # Also pick up citations from the node's scraped sources
                        if state_delta.get("citations"):
                            citations.extend(state_delta["citations"])
                        # Carry the honest provenance forward: whether the model
                        # wrote this, or it was synthesised from evidence after a
                        # refusal / empty reply.
                        report_quality = state_delta.get("report_quality", "")
                        report_user_message = state_delta.get("report_user_message", "")

                    # Evidence gaps and claims are emitted by the critic node.
                    # Persist the latest snapshot so the UI can show what the run
                    # could not establish, not just what it did.
                    if state_delta.get("evidence_gaps"):
                        evidence_gaps = state_delta["evidence_gaps"]
                    if state_delta.get("evidence_claims"):
                        evidence_claims = state_delta["evidence_claims"]

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

                    completed_count = sum(1 for n in nodes_map.values() if n["status"] == "completed")
                    progress_pct = int(10 + (completed_count / len(_TOPOLOGY)) * 80)

                    await self.job_state_manager.update_job(
                        job_id,
                        {
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
                        },
                    )

            # Publish terminal COMPLETED state
            _add_log(
                "JOB_COMPLETED",
                "success",
                {
                    "job_id": job_id,
                    "entities_mined": len(entities),
                    "sources_retrieved": len(citations),
                    "final_report_chars": len(final_report),
                },
            )

            # Build the canonical block document from what the run produced.
            # Never synthesised content: if the model refused, the document is
            # built from the evidence fallback exactly as the user saw it.
            if final_report:
                try:
                    report_document = ReportDocument.from_markdown(
                        job_id=job_id,
                        query=query,
                        markdown=final_report,
                        claims=evidence_claims,
                        entities=entities,
                        gaps=evidence_gaps,
                        quality=report_quality,
                        user_message=report_user_message,
                    ).to_dict()
                except Exception:  # pragma: no cover - defensive
                    # A malformed report must not fail an otherwise complete
                    # job; the Markdown string is still delivered.
                    log.warning("report_document.build_failed", job_id=job_id)

            await self.job_state_manager.update_job(
                job_id,
                {
                    "status": ResearchJobStatus.COMPLETED,
                    "progress": 100,
                    "nodes": [{**n, "status": "completed"} for n in nodes_map.values()],
                    "final_report": final_report,
                    "report_document": report_document,
                    "report_quality": report_quality,
                    "report_user_message": report_user_message,
                    "evidence_gaps": evidence_gaps,
                    "evidence_claims": evidence_claims,
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
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )

            log.info("Research job completed", job_id=job_id)

        except Exception as exc:
            log.error("Research job failed", job_id=job_id, error=str(exc))
            _add_log("JOB_FAILED", "error", {"error": str(exc)})
            await self.job_state_manager.update_job(
                job_id,
                {
                    "status": ResearchJobStatus.FAILED,
                    "progress": 100,
                    "nodes": list(nodes_map.values()),
                    "error": str(exc),
                    "logs": logs,
                    "entities": entities,
                    "relations": relations,
                    "chunks": chunks,
                    "citations": list(dict.fromkeys(citations)),
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )


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
            "timestamp": datetime.now(UTC).isoformat(),
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

    # LangGraph emits None for a node that returns an empty dict (e.g.
    # gap_analyst_node on its no-gaps path), so treat a missing delta as
    # "no usage recorded" instead of raising.
    messages = (state_delta or {}).get("messages", [])
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
            response_meta.get("model_name") or response_meta.get("model") or "gemini-1.5-flash"  # conservative fallback
        )
        total_cost += calculate_cost(model_name, input_tokens, output_tokens)

    return total_tokens, total_cost
