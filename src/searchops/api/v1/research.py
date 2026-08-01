"""
Research REST API Endpoints (v1).

Routes:
    POST   /api/v1/research/            Start a new research job
    GET    /api/v1/research/{job_id}    Get job status + result
    DELETE /api/v1/research/{job_id}    Cancel / forget a job (cache eviction)
"""

from __future__ import annotations

import structlog
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
