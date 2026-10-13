"""
ARQ Worker Configuration for Background Async Job Execution.
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

log = structlog.get_logger(__name__)


async def background_scrape_task(ctx: dict[str, Any], url: str) -> dict[str, Any]:
    """Background worker task for asynchronous URL scraping."""
    log.info("Executing background scrape task", url=url)
    return {"url": url, "status": "completed"}


async def background_graph_index_task(ctx: dict[str, Any], entity_data: dict[str, Any]) -> dict[str, Any]:
    """Background worker task for asynchronous Knowledge Graph indexing."""
    log.info("Executing background graph index task", entity=entity_data.get("name"))
    return {"status": "indexed"}


async def background_research_job(
    ctx: dict[str, Any],
    job_id: str,
    query: str,
    depth: str,
    max_sources: int,
    scrape_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Background worker task for autonomous research job execution.

    ``scrape_plan`` must be forwarded, not reconstructed. Without it the worker
    runs the pipeline with an empty plan and ``scrape_node`` silently falls back
    to its 3-URL cap — the user's configured page budget is discarded and the
    report is built from a third of the requested evidence. The job state
    already holds the authoritative plan, so read it from there rather than
    trusting the queue payload to stay in sync.
    """
    log.info("Executing background research job task", job_id=job_id, query=query)
    from searchops.bootstrap.container import ApplicationContainer
    from searchops.core.context.execution import ExecutionContext

    service = ApplicationContainer.create().research_service

    if not scrape_plan:
        job = await service.job_state_manager.get_job(job_id)
        scrape_plan = (job or {}).get("scrape_plan") or None

    await service._execute_research(job_id, query, depth, max_sources, ExecutionContext.create(), scrape_plan)
    return {"job_id": job_id, "status": "completed"}


class WorkerSettings:
    """ARQ Worker process settings."""

    functions: ClassVar[list[Any]] = [
        background_scrape_task,
        background_graph_index_task,
        background_research_job,
    ]
    max_jobs = 10
    poll_delay = 0.5
