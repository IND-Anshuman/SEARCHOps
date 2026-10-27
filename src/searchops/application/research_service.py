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
import contextlib
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from searchops.application.report_document import ReportDocument
from searchops.core.context.execution import ExecutionContext
from searchops.core.context.research import ReportType, ResearchConfig, ResearchDepth, ScrapePlan
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter
from searchops.llm.tokenizer import count_tokens
from searchops.orchestration.graphs.deep_research import (
    GRAPH_TOPOLOGY,
    build_deep_research_graph,
)
from searchops.orchestration.passages import PassageIndex, passages_from_state
from searchops.scraping.factory import build_scraping_pipeline
from searchops.search.aggregator import FederatedSearchAggregator

if TYPE_CHECKING:
    from searchops.application.job_state_manager import JobStateManager
    from searchops.infrastructure.cache.redis import RedisCache
    from searchops.orchestration.states.research_state import ResearchState
    from searchops.scraping.factory import PageFetcher

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


class JobLimitExceededError(Exception):
    """The user already has the maximum number of unfinished research runs."""

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"You already have {limit} research run(s) in progress. Wait for one to finish before starting another."
        )
        self.limit = limit


class DailyJobLimitExceededError(JobLimitExceededError):
    """The user has started the maximum number of runs allowed today."""

    def __init__(self, limit: int) -> None:
        Exception.__init__(
            self, f"You have started {limit} research runs today, the daily limit. It resets at 00:00 UTC."
        )
        self.limit = limit


_TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled"})
# A run interrupted by a restart is started again this many times; a run that
# keeps dying (one that crashes its process, say) is then reported as failed.
_MAX_RESUMES = 1
_WATCHDOG_INTERVAL_SECONDS = 20


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
        scraping_pipeline: PageFetcher | None = None,
        extractor: EntityExtractor | None = None,
        arq_pool: Any | None = None,
    ) -> None:
        self.cache = cache
        self.job_state_manager = job_state_manager
        from searchops.llm.cache import LLMResponseCache

        llm_cache = LLMResponseCache(cache_backend=cache) if cache else None
        self.llm_router = llm_router or LLMRouter(cache=llm_cache)
        self.aggregator = aggregator or FederatedSearchAggregator()
        self.scraping_pipeline = scraping_pipeline or build_scraping_pipeline(cache=cache)
        self.extractor = extractor or EntityExtractor(self.llm_router)
        self.arq_pool = arq_pool
        # Strong refs to background tasks so the event loop doesn't GC them mid-flight
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # Unfinished jobs per owner, for the concurrency cap. Membership is
        # confirmed against persisted job state, so it stays correct when a
        # worker process (not this one) finishes the job.
        self._active_by_owner: dict[str, set[str]] = {}
        # Identifies this process in job leases (see JobStateManager).
        self.runner_id = uuid.uuid4().hex

    async def _enforce_job_limit(self, owner_id: str) -> None:
        """Concurrent and daily caps for a signed-in user (service callers are exempt).

        Concurrent runs are counted from this process's own starts plus the
        cluster-wide unfinished-job set, so the cap holds with several API
        processes. The daily count lives in Redis.
        """
        from searchops.config.settings import get_settings

        api = get_settings().api
        if not owner_id:
            return
        limit = api.max_concurrent_jobs_per_user
        if limit > 0:
            active = self._active_by_owner.setdefault(owner_id, set())
            candidates = active | set(await self.job_state_manager.unfinished_job_ids())
            running: set[str] = set()
            for job_id in candidates:
                state = await self.job_state_manager.get_job(job_id)
                raw = (state or {}).get("status", "")
                status = str(getattr(raw, "value", raw)).lower()
                if state is None or status in _TERMINAL_JOB_STATES or state.get("owner_id") != owner_id:
                    active.discard(job_id)
                    continue
                running.add(job_id)
            if len(running) >= limit:
                raise JobLimitExceededError(limit)
        daily = api.max_jobs_per_user_per_day
        if daily > 0:
            started_today = await self.job_state_manager.count_daily_start(owner_id)
            if started_today is not None and started_today > daily:
                raise DailyJobLimitExceededError(daily)

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
        report_type: ReportType | str | None = None,
        report_structure_prompt: str | None = None,
        target_pages: int | None = None,
        report_theme: str | None = None,
        owner_id: str = "",
    ) -> str:
        """Create a research job and schedule async execution. Returns job_id.

        ``mode`` selects the run shape (precision | corpus). Its resolved
        :class:`ScrapePlan` is stored on the job so every downstream stage can
        read its own parameters instead of re-deriving the mode.

        ``config`` is the user's tuning. When supplied it overrides the plan's
        breadth and depth; the plan's other fields (relevance filter,
        verification sampling, report shape) stay mode-owned because they are
        structural choices, not knobs a user should have to reason about.

        ``report_type`` may be given either at the top level or inside
        ``config``. The explicit argument wins, because a caller that passes it
        directly has stated the value most recently and most specifically;
        reading only ``config`` silently ignored the top-level field.
        """
        await self._enforce_job_limit(owner_id)
        job_id = str(uuid.uuid4())
        exec_ctx = context or ExecutionContext.create()
        if owner_id:
            self._active_by_owner.setdefault(owner_id, set()).add(job_id)

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

        # Resolve the report shape: explicit argument, then config, then default.
        resolved_report_type = str(
            report_type or (config.report_type if config is not None else None) or ReportType.AUTO
        )
        resolved_structure_prompt = (
            report_structure_prompt
            if report_structure_prompt is not None
            else (config.report_structure_prompt if config is not None else "")
        )
        resolved_target_pages = (
            target_pages if target_pages is not None else (config.target_pages if config is not None else 12)
        )
        resolved_report_theme = (
            report_theme if report_theme is not None else (config.report_theme if config is not None else "default")
        )

        initial_state: dict[str, Any] = {
            "job_id": job_id,
            # The Clerk user who started the job; only they (and API-key
            # service callers) can read it. Empty for service-started jobs.
            "owner_id": owner_id,
            "status": ResearchJobStatus.PENDING,
            "query": query,
            "depth": depth,
            "mode": plan.mode.value,
            "scrape_plan": plan.model_dump(mode="json"),
            "max_sources": max_sources,
            "report_type": resolved_report_type,
            "report_structure_prompt": resolved_structure_prompt,
            "target_pages": resolved_target_pages,
            "report_theme": resolved_report_theme,
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
        # Held before the run is scheduled, so a watchdog pass in between cannot
        # mistake the new job for an orphan and start it a second time.
        await self.job_state_manager.claim_lease(job_id, self.runner_id)

        if self.arq_pool:
            try:
                await self.arq_pool.enqueue_job(
                    "background_research_job",
                    job_id,
                    query,
                    depth,
                    max_sources,
                    plan.model_dump(mode="json"),
                )
                log.info("Research job enqueued to ARQ pool", job_id=job_id)
            except Exception as exc:
                log.warning(
                    "ARQ enqueue failed; using inline async task",
                    job_id=job_id,
                    error=str(exc),
                )
                task = asyncio.create_task(
                    self._execute_research(job_id, query, depth, max_sources, exec_ctx, plan.model_dump(mode="json"))
                )
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
        else:
            task = asyncio.create_task(
                self._execute_research(job_id, query, depth, max_sources, exec_ctx, plan.model_dump(mode="json"))
            )
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
        scrape_plan: dict[str, Any] | None = None,
    ) -> None:
        """Run the pipeline under a hard time ceiling (``API_JOB_MAX_RUNTIME_SECONDS``).

        A run that exceeds it is cancelled and recorded as failed, so a hung
        provider cannot hold a worker, a user's concurrency slot and the LLM
        budget indefinitely.
        """
        from searchops.config.settings import get_settings

        ceiling = get_settings().api.job_max_runtime_seconds
        heartbeat = asyncio.create_task(self._hold_lease(job_id))
        try:
            if ceiling > 0:
                await asyncio.wait_for(
                    self._run_research(job_id, query, depth, max_sources, context, scrape_plan), timeout=ceiling
                )
            else:
                await self._run_research(job_id, query, depth, max_sources, context, scrape_plan)
        except TimeoutError:
            log.error("Research job exceeded its time limit", job_id=job_id, limit_seconds=ceiling)
            await self.job_state_manager.update_job(
                job_id,
                {
                    "status": ResearchJobStatus.FAILED,
                    "progress": 100,
                    "error": f"The run took longer than {ceiling // 60} minutes and was stopped.",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await self.job_state_manager.release_lease(job_id, self.runner_id)

    async def _hold_lease(self, job_id: str) -> None:
        """Keep this process's lease on the job fresh while the run lasts."""
        from searchops.application.job_state_manager import LEASE_TTL_SECONDS

        while True:
            await self.job_state_manager.renew_lease(job_id, self.runner_id)
            await asyncio.sleep(LEASE_TTL_SECONDS / 3)

    # ── Restart recovery ──────────────────────────────────────────────────────

    async def resume_interrupted_jobs(self) -> list[str]:
        """Restart unfinished jobs whose process died. Returns the ids resumed.

        A job is orphaned when it is unfinished but nobody holds its lease. The
        lease claim is atomic, so with several API processes exactly one of them
        picks each orphan up. The run starts over from the beginning (there are
        no per-node checkpoints); a job already resumed ``_MAX_RESUMES`` times is
        reported as failed instead.
        """
        from searchops.application.job_state_manager import INTERRUPTED_MESSAGE

        resumed: list[str] = []
        for job_id in await self.job_state_manager.unfinished_job_ids():
            state = await self.job_state_manager.get_raw(job_id)
            raw_status = (state or {}).get("status", "")
            if state is None or str(getattr(raw_status, "value", raw_status)).lower() in _TERMINAL_JOB_STATES:
                await self.job_state_manager.forget_unfinished(job_id)
                continue
            if not await self.job_state_manager.claim_lease(job_id, self.runner_id):
                continue  # still running somewhere, or another process just claimed it
            attempts = int(state.get("resume_count") or 0)
            if attempts >= _MAX_RESUMES:
                log.warning("Interrupted job already resumed; marking failed", job_id=job_id, attempts=attempts)
                await self.job_state_manager.update_job(
                    job_id,
                    {
                        "status": ResearchJobStatus.FAILED,
                        "progress": 100,
                        "error": INTERRUPTED_MESSAGE,
                        "completed_at": datetime.now(UTC).isoformat(),
                    },
                )
                await self.job_state_manager.release_lease(job_id, self.runner_id)
                continue

            log.warning("Resuming job interrupted by a restart", job_id=job_id, attempt=attempts + 1)
            await self.job_state_manager.update_job(
                job_id,
                {
                    "status": ResearchJobStatus.PENDING,
                    "progress": 0,
                    "error": None,
                    "nodes": _build_initial_nodes(),
                    "resume_count": attempts + 1,
                    "resumed_at": datetime.now(UTC).isoformat(),
                },
            )
            owner = str(state.get("owner_id") or "")
            if owner:
                self._active_by_owner.setdefault(owner, set()).add(job_id)
            task = asyncio.create_task(
                self._execute_research(
                    job_id,
                    str(state.get("query") or ""),
                    str(state.get("depth") or "standard"),
                    int(state.get("max_sources") or 10),
                    ExecutionContext.create(),
                    state.get("scrape_plan") or None,
                )
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            resumed.append(job_id)
        return resumed

    async def run_watchdog(self, interval: float = _WATCHDOG_INTERVAL_SECONDS) -> None:
        """Look for orphaned jobs for as long as the process lives.

        Runs from start-up, so a redeploy picks up the previous process's runs
        once their leases lapse (at most ``LEASE_TTL_SECONDS``).
        """
        while True:
            try:
                await self.resume_interrupted_jobs()
            except Exception as exc:
                log.warning("Job watchdog pass failed", error=str(exc))
            await asyncio.sleep(interval)

    async def _run_research(
        self,
        job_id: str,
        query: str,
        depth: str,
        max_sources: int,
        context: ExecutionContext,
        scrape_plan: dict[str, Any] | None = None,
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
            # The report settings were stored on the job at creation. Read
            # them back here so both execution paths (inline task and ARQ
            # worker) hand them to the graph; previously neither did, so
            # every report used the defaults whatever the user chose.
            stored = await self.job_state_manager.get_job(job_id) or {}
            initial_state: ResearchState = {
                "query": query,
                "depth": ResearchDepth(depth),
                "max_sources": max_sources,
                "scrape_plan": scrape_plan or {},
                "correlation_id": context.correlation_id,
                "iteration": 0,
                "target_pages": int(stored.get("target_pages") or 12),
                "report_structure_prompt": str(stored.get("report_structure_prompt") or ""),
                "report_type": str(stored.get("report_type") or "auto"),
                "report_theme": str(stored.get("report_theme") or "default"),
            }
            usage_before = _usage_snapshot(self.llm_router)
            last_tick = time.perf_counter()
            all_passages: list[Any] = []
            report_sources: list[dict[str, Any]] = []
            verification_stats: dict[str, Any] = {}
            searched_queries: list[str] = []

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
            # Evidence-quality metrics from the evaluation node. Persisted with
            # the job so the trust surfaces can show measured coverage and
            # hallucination rather than inferring quality from a count.
            evaluation_stats: dict[str, Any] = {}
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

                    # Nodes run one at a time, so the time since the previous
                    # update is this node's wall-clock latency. Nodes re-run on
                    # reflection loops; latency and cost accumulate per node.
                    latency_ms = int((wall_clock_now - last_tick) * 1000)
                    last_tick = wall_clock_now
                    node_start_times.pop(node_name, None)

                    # Real token use: diff the router's cumulative counters
                    # around the node (falls back to message metadata).
                    usage_now = _usage_snapshot(self.llm_router)
                    node_tokens = int(
                        usage_now["prompt_tokens"]
                        + usage_now["completion_tokens"]
                        - usage_before["prompt_tokens"]
                        - usage_before["completion_tokens"]
                    )
                    node_cost = max(0.0, usage_now["cost_usd"] - usage_before["cost_usd"])
                    usage_before = usage_now
                    if node_tokens == 0:
                        node_tokens, node_cost = _extract_token_usage(state_delta)
                    accumulated_token_used += node_tokens
                    accumulated_cost += node_cost

                    prev = nodes_map[node_name]
                    nodes_map[node_name].update(
                        {
                            "status": "completed",
                            "latency_ms": int(prev.get("latency_ms", 0) or 0) + latency_ms,
                            "token_cost": round(float(prev.get("token_cost", 0.0) or 0.0) + node_cost, 6),
                            "tokens": int(prev.get("tokens", 0) or 0) + node_tokens,
                            "runs": int(prev.get("runs", 0) or 0) + 1,
                            "output_payload": _summarise_delta(state_delta),
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
                                    "type": ent.entity_type,
                                    "summary": ent.description,
                                    "confidence_score": ent.confidence,
                                }
                            )

                    # Accumulate relations
                    if state_delta.get("relations"):
                        for rel in state_delta["relations"]:
                            relations.append(
                                {
                                    "id": rel.id,
                                    "source": rel.source_id,
                                    "target": rel.target_id,
                                    "source_canonical_id": rel.source_canonical_id,
                                    "target_canonical_id": rel.target_canonical_id,
                                    "relation_type": rel.relation_type,
                                    "description": rel.description,
                                }
                            )

                    if state_delta.get("searched_queries"):
                        searched_queries.extend(state_delta["searched_queries"])

                    # Evidence passages: the page text the run actually read,
                    # scored for relevance to the question (BM25, measured).
                    if state_delta.get("passages"):
                        all_passages.extend(passages_from_state(state_delta["passages"]))
                        chunks = _passage_chunks(all_passages, query, evidence_claims)

                    if state_delta.get("verification_stats"):
                        verification_stats = state_delta["verification_stats"]

                    # Capture the actual LLM-generated report (never a template)
                    if node_name == "report_writer" and state_delta.get("final_report"):
                        final_report = state_delta["final_report"]
                        # Citations are the report's numbered source registry:
                        # the pages that back its claims, not every search hit.
                        report_sources = list(state_delta.get("report_sources") or [])
                        citations = [s["url"] for s in report_sources] or list(state_delta.get("citations") or [])
                        # Carry the honest provenance forward: whether the model
                        # wrote this, or it was synthesised from evidence after a
                        # refusal / empty reply.
                        report_quality = state_delta.get("report_quality", "")
                        report_user_message = state_delta.get("report_user_message", "")

                    # Evidence gaps and claims are emitted by the critic node.
                    # Persist the latest snapshot so the UI can show what the run
                    # could not establish, not just what it did.
                    #
                    # IMPORTANT: the gap_analyst node clears evidence_gaps to []
                    # to stop the reflection loop re-firing the same gaps. The
                    # LangGraph stream delivers that clearing delta as its own
                    # update. If we overwrote `evidence_gaps` with every [] that
                    # flies past, every looped run would report zero gaps — the
                    # "Next" tab would falsely declare the report complete.
                    # Only capture real content: a non-empty gap list from the
                    # critic survives; the gap_analyst's [] clear is ignored.
                    if state_delta.get("evidence_gaps"):
                        evidence_gaps = state_delta["evidence_gaps"]
                    if state_delta.get("evidence_claims"):
                        # Deltas carry new or updated claims; merge by id so
                        # earlier rounds' claims are not lost.
                        merged_claims = {c.id: c for c in evidence_claims}
                        for c in state_delta["evidence_claims"]:
                            merged_claims[c.id] = c
                        evidence_claims = list(merged_claims.values())
                        if all_passages:
                            chunks = _passage_chunks(all_passages, query, evidence_claims)

                    # Evidence-quality metrics from the evaluation node: citation
                    # coverage, hallucination rate, claim status mix.
                    if state_delta.get("evaluation_stats"):
                        evaluation_stats = state_delta["evaluation_stats"]

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
                    "evaluation_stats": evaluation_stats,
                    "verification_stats": verification_stats,
                    "report_sources": report_sources,
                    "searched_queries": searched_queries,
                    "citations": list(dict.fromkeys(citations)),
                    "entity_count": len(entities),
                    "source_count": len(report_sources) or len(dict.fromkeys(citations)),
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


def _usage_snapshot(router: Any) -> dict[str, float]:
    usage = getattr(router, "usage", None)
    base = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0}
    if isinstance(usage, dict):
        for k in base:
            try:
                base[k] = float(usage.get(k, 0) or 0)
            except (TypeError, ValueError):
                continue
    return base


def _summarise_delta(delta: dict[str, Any]) -> dict[str, str]:
    """Short, human-readable view of a node's output for the inspector."""
    out: dict[str, str] = {}
    for k, v in delta.items():
        if k in ("messages", "round_documents", "evidence_corpus"):
            continue
        if isinstance(v, list):
            out[k] = f"{len(v)} item(s)" + (f": {str(v[0])[:160]}" if v else "")
        else:
            out[k] = str(v)[:200]
    return out


_MAX_CHUNKS = 200


def _passage_chunks(passages: list[Any], query: str, claims: list[Any]) -> list[dict[str, Any]]:
    """The run's evidence passages, ranked by measured relevance to the query.

    ``similarity_score`` is the passage's BM25 score against the question,
    normalised to the best passage (1.0); ``token_count`` is counted, not
    assumed; ``cited`` marks passages a claim's verified quote came from.
    """
    if not passages:
        return []
    cited = {getattr(c, "passage_id", "") for cl in claims for c in getattr(cl, "citations", []) or []}
    index = PassageIndex(passages)
    scored = [(p, index.score(query, i)) for i, p in enumerate(passages)]
    top = max((sc for _, sc in scored), default=0.0) or 1.0
    scored.sort(key=lambda x: (x[0].id not in cited, -x[1]))
    return [
        {
            "id": p.id,
            "document_title": p.title or p.url,
            "source_url": p.url,
            "similarity_score": round(sc / top, 4),
            "token_count": int(count_tokens(p.text)),
            "chunk_preview": p.text[:600],
            "cited": p.id in cited,
        }
        for p, sc in scored[:_MAX_CHUNKS]
    ]


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
