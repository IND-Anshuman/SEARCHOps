"""
Research REST API Endpoints (v1).

Routes:
    POST   /api/v1/research/              Start a new research job
    GET    /api/v1/research/{job_id}      Get job status + full result
    DELETE /api/v1/research/{job_id}      Evict job from cache
    GET    /api/v1/research/{job_id}/graph    Knowledge graph
    GET    /api/v1/research/{job_id}/chunks   Vector chunks
    GET    /api/v1/research/{job_id}/logs     Event logs

All handlers resolve ResearchApplicationService from the shared DI
container stored in request.app.state.container. No new service instances
are ever created inside a request handler.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


# ── Request / Response schemas ─────────────────────────────────────────────────

class StartResearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000)
    depth: str = Field(default="standard", description="shallow | standard | deep")
    max_sources: int = Field(default=10, ge=1, le=50)


class StartResearchResponse(BaseModel):
    job_id: str
    status: str
    message: str


class LangGraphNodeSchema(BaseModel):
    """Mirrors the node dict written by ResearchApplicationService._execute_research."""
    id: str
    label: str
    type: str
    status: str
    latency_ms: int = 0
    token_cost: float = 0.0
    retries: int = 0
    timestamp: str = ""
    prompt: str = ""
    input_payload: dict[str, Any] = {}
    output_payload: dict[str, Any] = {}


class JobStatusResponse(BaseModel):
    """Complete job state — every field stored in Redis is declared here.

    FastAPI serializes only declared fields; undeclared fields are silently
    stripped. This schema must stay in sync with the dict produced by
    ResearchApplicationService._execute_research.
    """
    job_id: str
    status: str
    query: str | None = None
    depth: str = "standard"
    progress: int = 0
    # Report
    final_report: str | None = None
    citations: list[str] = []
    entity_count: int = 0
    source_count: int = 0
    # Telemetry (real measurements, never hardcoded)
    token_used: int = 0
    token_budget: int = 150000
    cost_current: float = 0.0
    cost_budget: float = 5.0
    # Graph nodes topology
    nodes: list[LangGraphNodeSchema] = []
    # Error
    error: str | None = None
    # Timestamps
    created_at: str | None = None
    start_time: str | None = None
    completed_at: str | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class KGNodeSchema(BaseModel):
    id: str
    canonicalId: str = Field(default="", validation_alias=AliasChoices("canonical_id", "canonicalId"), serialization_alias="canonicalId")
    name: str
    type: str
    summary: str = ""

    model_config = ConfigDict(populate_by_name=True)


class KGEdgeSchema(BaseModel):
    id: str
    source: str
    target: str
    relation_type: str
    description: str = ""


class KnowledgeGraphResponse(BaseModel):
    nodes: list[KGNodeSchema] = []
    edges: list[KGEdgeSchema] = []


class VectorChunkSchema(BaseModel):
    id: str
    documentTitle: str = Field(default="", validation_alias=AliasChoices("document_title", "documentTitle"), serialization_alias="documentTitle")
    sourceUrl: str = Field(default="", validation_alias=AliasChoices("source_url", "sourceUrl"), serialization_alias="sourceUrl")
    similarityScore: float = Field(default=0.0, validation_alias=AliasChoices("similarity_score", "similarityScore"), serialization_alias="similarityScore")
    tokenCount: int = Field(default=0, validation_alias=AliasChoices("token_count", "tokenCount"), serialization_alias="tokenCount")
    chunkPreview: str = Field(default="", validation_alias=AliasChoices("chunk_preview", "chunkPreview"), serialization_alias="chunkPreview")

    model_config = ConfigDict(populate_by_name=True)


class VectorChunksResponse(BaseModel):
    chunks: list[VectorChunkSchema] = []


class LogItemSchema(BaseModel):
    id: str
    stream: str = ""
    eventType: str = Field(default="", validation_alias=AliasChoices("event_type", "eventType"), serialization_alias="eventType")
    correlationId: str = Field(default="", validation_alias=AliasChoices("correlation_id", "correlationId"), serialization_alias="correlationId")
    timestamp: str = ""
    payload: dict[str, Any] = {}
    level: str = "info"

    model_config = ConfigDict(populate_by_name=True)


class LogsResponse(BaseModel):
    logs: list[LogItemSchema] = []


# ── Dependency: resolve singleton service from container ───────────────────────

def _get_service(request: Request) -> ResearchApplicationService:
    """Resolve the shared ResearchApplicationService from the DI container.

    The container is stored in app.state during startup. Fallback to
    get_container() if app.state.container is not directly set.
    """
    from searchops.bootstrap.container import get_container
    try:
        return request.app.state.container.research_service
    except AttributeError:
        return get_container().research_service


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=StartResearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an autonomous research job",
)
async def start_research(
    payload: StartResearchRequest,
    service: ResearchApplicationService = Depends(_get_service),
) -> StartResearchResponse:
    job_id = await service.start_research(
        query=payload.query,
        depth=payload.depth,
        max_sources=payload.max_sources,
    )
    return StartResearchResponse(
        job_id=job_id,
        status=ResearchJobStatus.PENDING,
        message=f"Research job submitted. Connect to ws://.../ws/research/{job_id} for live updates.",
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get research job status and result",
)
async def get_research_status(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> JobStatusResponse:
    data = await service.get_job_status(job_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )
    return JobStatusResponse.model_validate(data)


@router.get(
    "/{job_id}/graph",
    response_model=KnowledgeGraphResponse,
    summary="Get Knowledge Graph for research job",
)
async def get_research_graph(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> KnowledgeGraphResponse:
    job_data = await service.get_job_status(job_id)
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )

    nodes: list[KGNodeSchema] = []
    edges: list[KGEdgeSchema] = []

    for e in job_data.get("entities", []):
        nodes.append(KGNodeSchema(
            id=e.get("id", ""),
            canonical_id=e.get("canonical_id", ""),
            name=e.get("name", ""),
            type=e.get("entity_type", "technology"),
            summary=e.get("description", ""),
        ))

    for idx, r in enumerate(job_data.get("relations", [])):
        edges.append(KGEdgeSchema(
            id=r.get("id") or f"edge_{idx}",
            source=r.get("source_canonical_id") or r.get("source_id", ""),
            target=r.get("target_canonical_id") or r.get("target_id", ""),
            relation_type=r.get("relation_type", "RELATED_TO"),
            description=r.get("description", ""),
        ))

    return KnowledgeGraphResponse(nodes=nodes, edges=edges)


@router.get(
    "/{job_id}/chunks",
    response_model=VectorChunksResponse,
    summary="Get retrieved Vector Chunks for research job",
)
async def get_research_chunks(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> VectorChunksResponse:
    job_data = await service.get_job_status(job_id)
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )

    chunks = [
        VectorChunkSchema.model_validate(c)
        for c in job_data.get("chunks", [])
    ]
    return VectorChunksResponse(chunks=chunks)


@router.get(
    "/{job_id}/logs",
    response_model=LogsResponse,
    summary="Get event logs for research job",
)
async def get_research_logs(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> LogsResponse:
    job_data = await service.get_job_status(job_id)
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )

    logs = [
        LogItemSchema.model_validate(log_item)
        for log_item in job_data.get("logs", [])
    ]
    return LogsResponse(logs=logs)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Evict a research job from cache",
)
async def delete_research_job(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> None:
    await service.job_state_manager.delete_job(job_id)
