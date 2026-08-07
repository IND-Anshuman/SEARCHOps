"""
Research REST API Endpoints (v1).

Routes:
    POST   /api/v1/research/            Start a new research job
    GET    /api/v1/research/{job_id}    Get job status + result
    DELETE /api/v1/research/{job_id}    Cancel / forget a job (cache eviction)
"""

from __future__ import annotations

import structlog
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


# ── Request / Response schemas ────────────────────────────────────────────────

class StartResearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000, description="The research question")
    depth: str = Field(default="standard", description="shallow | standard | deep")
    max_sources: int = Field(default=10, ge=1, le=50)


class StartResearchResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    query: str | None = None
    progress: int = 0
    final_report: str | None = None
    citations: list[str] = []
    entity_count: int = 0
    source_count: int = 0
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class KGNodeSchema(BaseModel):
    id: str
    canonicalId: str
    name: str
    type: str
    summary: str


class KGEdgeSchema(BaseModel):
    id: str
    source: str
    target: str
    relation_type: str
    description: str


class KnowledgeGraphResponse(BaseModel):
    nodes: list[KGNodeSchema] = []
    edges: list[KGEdgeSchema] = []


class VectorChunkSchema(BaseModel):
    id: str
    documentTitle: str
    sourceUrl: str
    similarityScore: float
    tokenCount: int
    chunkPreview: str


class VectorChunksResponse(BaseModel):
    chunks: list[VectorChunkSchema] = []


class LogItemSchema(BaseModel):
    id: str
    stream: str
    eventType: str
    correlationId: str
    timestamp: str
    payload: dict[str, Any] = {}
    level: str


class LogsResponse(BaseModel):
    logs: list[LogItemSchema] = []


# ── Dependency helper (replaced by DI container in production) ────────────────

def _get_service() -> ResearchApplicationService:
    return ResearchApplicationService()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=StartResearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an autonomous research job",
    description="Submits a research query; returns a job_id for polling or streaming.",
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
        message="Research job submitted. Poll GET /research/{job_id} for results.",
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
    return JobStatusResponse(**data)


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

    # Check if graph exists in Neo4j via repository
    # Fall back to Redis cached entities/relations if Neo4j is offline or empty
    nodes: list[KGNodeSchema] = []
    edges: list[KGEdgeSchema] = []

    cached_entities = job_data.get("entities", [])
    cached_relations = job_data.get("relations", [])

    for e in cached_entities:
        nodes.append(KGNodeSchema(
            id=e.get("id", ""),
            canonicalId=e.get("canonical_id", ""),
            name=e.get("name", ""),
            type=e.get("entity_type", "technology"),
            summary=e.get("description", "")
        ))

    for idx, r in enumerate(cached_relations):
        edges.append(KGEdgeSchema(
            id=r.get("id") or f"edge_{idx}",
            source=r.get("source_canonical_id") or r.get("source_id", ""),
            target=r.get("target_canonical_id") or r.get("target_id", ""),
            relation_type=r.get("relation_type", "RELATED_TO"),
            description=r.get("description", "")
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

    # Fall back to chunks stored in cache
    chunks_data = job_data.get("chunks", [])
    chunks = []
    for c in chunks_data:
        chunks.append(VectorChunkSchema(
            id=c.get("id", ""),
            documentTitle=c.get("documentTitle", ""),
            sourceUrl=c.get("sourceUrl", ""),
            similarityScore=c.get("similarityScore", 0.0),
            tokenCount=c.get("tokenCount", 0),
            chunkPreview=c.get("chunkPreview", "")
        ))

    return VectorChunksResponse(chunks=chunks)


@router.get(
    "/{job_id}/logs",
    response_model=LogsResponse,
    summary="Get logs / event logs for research job",
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

    logs_data = job_data.get("logs", [])
    logs = []
    for l in logs_data:
        logs.append(LogItemSchema(
            id=l.get("id", ""),
            stream=l.get("stream", ""),
            eventType=l.get("eventType", ""),
            correlationId=l.get("correlationId", ""),
            timestamp=l.get("timestamp", ""),
            payload=l.get("payload", {}),
            level=l.get("level", "info")
        ))

    return LogsResponse(logs=logs)


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a research job from cache",
)
async def delete_research_job(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> None:
    if service.cache:
        await service.cache.delete(f"research:job:{job_id}")
