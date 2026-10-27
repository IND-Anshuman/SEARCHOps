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

import tempfile
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from searchops.application.exports.deck import export_deck
from searchops.application.exports.docx_export import export_docx
from searchops.application.exports.pdf_export import ExportUnavailableError, export_pdf, pdf_export_available
from searchops.application.exports.study import build_study_guide
from searchops.application.report_document import ReportDocument
from searchops.application.research_service import (
    DailyJobLimitExceededError,
    JobLimitExceededError,
    ResearchApplicationService,
    ResearchJobStatus,
)
from searchops.core.context.research import ReportTheme, ReportType, ResearchConfig, ResearchMode
from searchops.middleware.auth import current_principal, visible_job

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


# ── Request / Response schemas ─────────────────────────────────────────────────


class StartResearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000)
    depth: str = Field(default="standard", description="shallow | standard | deep")
    mode: str = Field(
        default="precision",
        description="precision (answer one question well) | corpus (build a dataset)",
    )
    max_sources: int = Field(default=10, ge=1, le=50)
    # User-tunable scraping configuration. Every field carries a hard bound, so
    # a request cannot ask for an unbounded crawl. An inconsistent plan (more
    # pages than the scrape budget allows) is rejected here rather than
    # silently truncated at run time.
    config: ResearchConfig | None = Field(
        default=None,
        description="Optional scraping/verification tuning. Omit for mode defaults.",
    )
    report_type: ReportType = Field(
        default=ReportType.AUTO,
        description=(
            "Structure of the finished report. Determines its sections, length and tone. auto infers from the query."
        ),
    )
    report_structure_prompt: str = Field(
        default="",
        max_length=2000,
        description="Custom report structure instructions (used when report_type=custom).",
    )
    target_pages: int = Field(
        default=12,
        ge=1,
        le=100,
        description="Target page count. Soft target: the structurer never pads to hit it, but never stops short either.",
    )
    report_theme: ReportTheme = Field(
        default=ReportTheme.DEFAULT,
        description="PDF theme variant (default, minimal, academic, executive).",
    )

    @field_validator("config")
    @classmethod
    def _config_must_be_consistent(cls, v: ResearchConfig | None) -> ResearchConfig | None:
        if v is None:
            return None
        problems = v.validate_against_budget()
        if problems:
            raise ValueError(" ".join(problems))
        return v


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
    mode: str = "precision"
    progress: int = 0
    # How many times the run was restarted after its process died (a redeploy
    # or crash), and when it last was. 0 for a run that went straight through.
    resume_count: int = 0
    resumed_at: str | None = None
    # Report
    final_report: str | None = None
    # Why the report looks the way it does. Populated by report_writer via
    # llm/report_shaper.py: "ok" when the model wrote it, otherwise a degraded
    # state (refusal / raw_empty / no_sections / no_evidence / synthesized)
    # with a plain-language explanation for the user. Both must reach the
    # client so a synthesised report is never shown as if the model wrote it.
    report_quality: str | None = None
    report_user_message: str | None = None
    # The canonical block model for this report. Declared here because
    # JobStatusResponse sets extra="ignore" — an undeclared field is silently
    # stripped, which is how report_quality was lost before. Every block-level
    # feature (export, where-to-go-next, study guide, trust header) reads this.
    report_document: dict[str, Any] | None = None
    # Structured evidence: what the run established, and what it could not.
    evidence_gaps: list[dict[str, Any]] = []
    evidence_claims: list[dict[str, Any]] = []
    # Evidence-quality metrics measured by the evaluation node: citation
    # coverage, hallucination rate, claim status mix.
    evaluation_stats: dict[str, Any] = {}
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
    # Event logs — the console and the chat telemetry stream read these.
    # Undeclared here they were silently stripped by `extra="ignore"`, so a
    # reloaded job rendered an empty console while the data existed in Redis.
    logs: list[LogItemSchema] = []
    # Knowledge-graph entities/relations, exposed in the same camelCase shape
    # the graph endpoint already uses, so the explorer survives a reload.
    entities: list[KGNodeSchema] = []
    relations: list[KGEdgeSchema] = []
    # Retrieved vector chunks for the vector inspector.
    chunks: list[VectorChunkSchema] = []
    # Error
    error: str | None = None
    # Timestamps
    created_at: str | None = None
    start_time: str | None = None
    completed_at: str | None = None
    # Report shape, echoed back so a reloaded job can reproduce its own export.
    report_theme: str | None = None
    report_type: str | None = None
    target_pages: int | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}


from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class KGNodeSchema(BaseModel):
    id: str
    canonicalId: str = Field(
        default="",
        validation_alias=AliasChoices("canonical_id", "canonicalId"),
        serialization_alias="canonicalId",
    )
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
    documentTitle: str = Field(
        default="",
        validation_alias=AliasChoices("document_title", "documentTitle"),
        serialization_alias="documentTitle",
    )
    sourceUrl: str = Field(
        default="",
        validation_alias=AliasChoices("source_url", "sourceUrl"),
        serialization_alias="sourceUrl",
    )
    similarityScore: float = Field(
        default=0.0,
        validation_alias=AliasChoices("similarity_score", "similarityScore"),
        serialization_alias="similarityScore",
    )
    tokenCount: int = Field(
        default=0,
        validation_alias=AliasChoices("token_count", "tokenCount"),
        serialization_alias="tokenCount",
    )
    chunkPreview: str = Field(
        default="",
        validation_alias=AliasChoices("chunk_preview", "chunkPreview"),
        serialization_alias="chunkPreview",
    )

    model_config = ConfigDict(populate_by_name=True)


class VectorChunksResponse(BaseModel):
    chunks: list[VectorChunkSchema] = []


class LogItemSchema(BaseModel):
    id: str
    stream: str = ""
    eventType: str = Field(
        default="",
        validation_alias=AliasChoices("event_type", "eventType"),
        serialization_alias="eventType",
    )
    correlationId: str = Field(
        default="",
        validation_alias=AliasChoices("correlation_id", "correlationId"),
        serialization_alias="correlationId",
    )
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
        service: ResearchApplicationService = request.app.state.container.research_service
        return service
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
    # Validate the mode here so a bad value is a clear 422 rather than a 500
    # from deep inside job execution.
    try:
        ResearchMode(payload.mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid mode {payload.mode!r}. Expected one of: {', '.join(m.value for m in ResearchMode)}."),
        ) from exc

    try:
        job_id = await service.start_research(
            query=payload.query,
            depth=payload.depth,
            max_sources=payload.max_sources,
            mode=payload.mode,
            config=payload.config,
            report_type=payload.report_type,
            report_structure_prompt=payload.report_structure_prompt,
            target_pages=payload.target_pages,
            report_theme=payload.report_theme,
            owner_id=_owner_id(),
        )
    except JobLimitExceededError as exc:
        retry_after = "3600" if isinstance(exc, DailyJobLimitExceededError) else "60"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc), headers={"Retry-After": retry_after}
        ) from exc
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
    data = visible_job(await service.get_job_status(job_id))
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
    job_data = visible_job(await service.get_job_status(job_id))
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )

    nodes: list[KGNodeSchema] = []
    edges: list[KGEdgeSchema] = []

    for e in job_data.get("entities", []):
        nodes.append(
            KGNodeSchema(
                id=str(e.get("id", "")),
                canonical_id=e.get("canonical_id") or e.get("canonicalId") or "",
                name=e.get("name", ""),
                type=e.get("type") or e.get("entity_type") or "technology",
                summary=e.get("summary") or e.get("description") or "",
            )
        )

    for idx, r in enumerate(job_data.get("relations", [])):
        edges.append(
            KGEdgeSchema(
                id=r.get("id") or f"edge_{idx}",
                source=r.get("source_canonical_id") or r.get("source_id", ""),
                target=r.get("target_canonical_id") or r.get("target_id", ""),
                relation_type=r.get("relation_type", "RELATED_TO"),
                description=r.get("description", ""),
            )
        )

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
    job_data = visible_job(await service.get_job_status(job_id))
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )

    chunks = [VectorChunkSchema.model_validate(c) for c in job_data.get("chunks", [])]
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
    job_data = visible_job(await service.get_job_status(job_id))
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )

    logs = [LogItemSchema.model_validate(log_item) for log_item in job_data.get("logs", [])]
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
    if visible_job(await service.job_state_manager.get_job(job_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Research job '{job_id}' not found.")
    await service.job_state_manager.delete_job(job_id)


# ── Report export ────────────────────────────────────────────────────────────
#
# A report is only useful if the user can deliver it. Deck/Word/PDF are what
# people actually need to hand over, and both Microsoft and Google shipped the
# same conversions in 2026 — without them users paste into a competitor.
#
# The exports carry the claim→source binding into the artifact (PPTX speaker
# notes, DOCX footnotes), which a generic markdown converter cannot do because
# it has no binding to read from.

_EXPORT_DIR = Path(tempfile.gettempdir()) / "searchops_exports"

_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"
_PDF_FALLBACK_HEADER = "X-PDF-Fallback"


def _load_document(job_data: dict[str, Any]) -> ReportDocument:
    """Rebuild the block document for a job.

    Prefers the persisted ``report_document``. Falls back to parsing
    ``final_report`` so a job completed before the block model existed still
    exports — never a 500 on an older job.
    """
    raw = job_data.get("report_document")
    if raw:
        try:
            return ReportDocument.from_dict(raw)
        except Exception:  # pragma: no cover - defensive
            log.warning("export.document_rebuild_failed", job_id=job_data.get("job_id"))
    return ReportDocument.from_markdown(
        job_id=str(job_data.get("job_id", "")),
        query=str(job_data.get("query", "") or ""),
        markdown=str(job_data.get("final_report", "") or ""),
        quality=str(job_data.get("report_quality", "") or "ok"),
        user_message=str(job_data.get("report_user_message", "") or ""),
    )


def _owner_id() -> str:
    """The Clerk user starting a job; empty for API-key (service) callers."""
    principal = current_principal()
    return principal.user_id if principal is not None and principal.kind == "user" else ""


def _require_completed_job(job_data: dict[str, Any] | None, job_id: str) -> dict[str, Any]:
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )
    if not job_data.get("final_report"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job has not produced a report yet. Export is available once it completes.",
        )
    return job_data


async def _study_guide_for_export(
    doc: ReportDocument,
    service: ResearchApplicationService,
) -> Any:
    """Build the study guide the exporters render.

    The exporters used to look for ``doc._study_guide`` — an attribute nothing
    ever set — so the study-guide section silently never appeared in any PDF,
    DOCX or PPTX. Building it here, once, with the shared LLM router fixes
    that without the exporters acquiring a router of their own.

    Any failure returns ``None``: an export must never fail because the study
    guide could not be generated. When that happens the section is omitted,
    which is honest, rather than filled with invented questions.
    """
    try:
        return await build_study_guide(doc, router=service.llm_router)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("export.study_guide_failed", error=str(exc))
        return None


@router.get(
    "/{job_id}/export/deck",
    summary="Export the report as a PowerPoint deck (pass include_study_guide=false to skip the study guide)",
    response_class=FileResponse,
)
async def export_research_deck(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
    include_study_guide: bool = True,
) -> FileResponse:
    """Export a completed report as .pptx.

    Speaker notes carry the claims backing each slide, so a reader can check
    what they are shown.
    """
    job_data = _require_completed_job(visible_job(await service.job_state_manager.get_job(job_id)), job_id)
    doc = _load_document(job_data)
    guide = await _study_guide_for_export(doc, service) if include_study_guide else None
    try:
        path = export_deck(
            doc,
            out_dir=_EXPORT_DIR,
            title=doc.report_title(),
            study_guide=guide,
        )
    except Exception as exc:
        log.error("export.deck.failed", job_id=job_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deck export failed: {exc}",
        ) from exc
    return FileResponse(path=str(path), media_type=_PPTX_MIME, filename=path.name)


@router.get(
    "/{job_id}/export/docx",
    summary="Export the report as a Word document (pass include_study_guide=false to skip the study guide)",
    response_class=FileResponse,
)
async def export_research_docx(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
    include_study_guide: bool = True,
) -> FileResponse:
    """Export a completed report as .docx.

    Real heading styles and per-section claim footnotes, so the file survives
    into a compliance record or an email thread intact.
    """
    job_data = _require_completed_job(visible_job(await service.job_state_manager.get_job(job_id)), job_id)
    doc = _load_document(job_data)
    guide = await _study_guide_for_export(doc, service) if include_study_guide else None
    try:
        path = export_docx(
            doc,
            out_dir=_EXPORT_DIR,
            title=doc.report_title(),
            study_guide=guide,
        )
    except Exception as exc:
        log.error("export.docx.failed", job_id=job_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Word export failed: {exc}",
        ) from exc
    return FileResponse(path=str(path), media_type=_DOCX_MIME, filename=path.name)


@router.get(
    "/{job_id}/export/pdf",
    summary="Export the report as a PDF (pass include_study_guide=false to skip the study guide)",
    response_class=FileResponse,
)
async def export_research_pdf(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
    include_study_guide: bool = True,
) -> FileResponse:
    """Export a completed report as .pdf via headless Chromium.

    Fails loudly when Chromium is unavailable — an export that quietly returns
    an empty file is discovered only after the document has been sent.
    """
    job_data = _require_completed_job(visible_job(await service.job_state_manager.get_job(job_id)), job_id)
    # Checked before the study guide, which costs an LLM call: without a
    # browser on the server there is nothing to spend it on. The header tells
    # the web app to print the report page in the user's browser instead.
    if not pdf_export_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This deployment does not render PDFs on the server; print the report page from the browser.",
            headers={_PDF_FALLBACK_HEADER: "browser-print"},
        )
    doc = _load_document(job_data)
    guide = await _study_guide_for_export(doc, service) if include_study_guide else None
    try:
        path = await export_pdf(
            doc,
            out_dir=_EXPORT_DIR,
            title=doc.report_title(),
            theme=str(job_data.get("report_theme", "default") or "default"),
            study_guide=guide,
        )
    except ExportUnavailableError as exc:
        log.error("export.pdf.unavailable", job_id=job_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        log.error("export.pdf.failed", job_id=job_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF export failed: {exc}",
        ) from exc
    return FileResponse(path=str(path), media_type=_PDF_MIME, filename=path.name)


# ── Block operations ─────────────────────────────────────────────────────────
#
# The interactive layer. A report used to be one opaque Markdown string; it is
# now addressable blocks, and a user can act on any one of them. Each op
# returns the whole updated document so the client re-renders from a single
# source of truth rather than patching locally and drifting.
#
# Contract: an operation that cannot produce usable text leaves the document
# untouched and says why. A failed edit must never look like a successful one.

from searchops.application.exports.block_ops import apply_op


class BlockOpRequest(BaseModel):
    """Request body for a block operation."""

    op: str = Field(description="condense | deepen | rewrite | hide | unhide | split")
    at_char: int = Field(default=0, description="Split position, for op=split")


@router.post(
    "/{job_id}/blocks/{block_id}/op",
    summary="Apply an operation to one block of the report",
)
async def apply_block_operation(
    job_id: str,
    block_id: str,
    body: BlockOpRequest,
    service: ResearchApplicationService = Depends(_get_service),
) -> dict[str, Any]:
    """Condense, deepen, rewrite, hide, unhide or split one block.

    Returns the updated document. On failure ``ok`` is False and ``reason``
    explains why in language the UI can show a user; the document is returned
    unchanged.
    """
    job_data = visible_job(await service.job_state_manager.get_job(job_id))
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )
    if not job_data.get("final_report"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job has not produced a report yet.",
        )

    doc = _load_document(job_data)
    result = await apply_op(
        doc,
        block_id,
        body.op,
        router=service.llm_router,
        at_char=body.at_char,
    )

    if result.ok:
        # Persist so the edit survives a reload and reaches other clients.
        try:
            await service.job_state_manager.update_job(job_id, {"report_document": result.document.to_dict()})
        except Exception as exc:
            log.warning("block_op.persist_failed", job_id=job_id, error=str(exc))

    return result.to_dict()
