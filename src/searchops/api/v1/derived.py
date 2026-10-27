"""Derived-artifact endpoints: glossary, next steps, trust, study guide.

These surface evidence the research pipeline already computes but never showed
anyone:

- ``glossary`` — the entities the extractor found, shaped for reading.
- ``next-steps`` — the ``EvidenceGap`` objects the critic emitted, as clickable
  follow-ups. Users explicitly ask for this: "give me what you could not
  establish, and point me at where to look next."
- ``trust`` — a one-line, plain-language summary of how much of the report is
  actually supported, computed from real ``Claim.status`` values.
- ``study-guide`` / ``faq`` — generated from the report's own blocks.

Nothing here invents content. A glossary entry with no description stays
empty; an empty study guide says so rather than producing plausible-looking
questions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from searchops.application.exports.glossary import build_glossary
from searchops.application.exports.study import build_faq, build_study_guide
from searchops.application.report_document import (
    ReportDocument,
    trust_headline,
    trust_summary,
)
from searchops.middleware.auth import visible_job

if TYPE_CHECKING:
    from searchops.application.research_service import ResearchApplicationService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/derived", tags=["Derived"])


def _get_service(request: Request) -> ResearchApplicationService:
    """Resolve the shared ResearchApplicationService from the DI container.

    Delegates to the research module's resolver so both routers share one
    container and one service instance.
    """
    from searchops.api.v1.research import _get_service as research_service

    return research_service(request)


async def _load_job(job_id: str, service: ResearchApplicationService) -> dict[str, Any]:
    """Fetch a job, or raise the status that tells the user what to do next."""
    job_data = visible_job(await service.job_state_manager.get_job(job_id))
    if job_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research job '{job_id}' not found.",
        )
    if not job_data.get("final_report"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job has not produced a report yet. Derived artifacts are available once it completes.",
        )
    return job_data


def _document(job_data: dict[str, Any]) -> ReportDocument:
    raw = job_data.get("report_document")
    if raw:
        try:
            return ReportDocument.from_dict(raw)
        except Exception:  # pragma: no cover - defensive
            log.warning("derived.document_rebuild_failed", job_id=job_data.get("job_id"))
    return ReportDocument.from_markdown(
        job_id=str(job_data.get("job_id", "")),
        query=str(job_data.get("query", "") or ""),
        markdown=str(job_data.get("final_report", "") or ""),
        quality=str(job_data.get("report_quality", "") or "ok"),
        user_message=str(job_data.get("report_user_message", "") or ""),
    )


@router.get(
    "/{job_id}/glossary",
    summary="Glossary of entities extracted from the report's sources",
)
async def get_glossary(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> dict[str, Any]:
    """Entities as a glossary. Costs nothing — no LLM call.

    An entity with no description yields an empty definition rather than an
    invented one.
    """
    job_data = await _load_job(job_id, service)
    doc = _document(job_data)
    terms = build_glossary(doc)
    return {"terms": [t.to_dict() for t in terms]}


@router.get(
    "/{job_id}/next-steps",
    summary="Coverage gaps the research run could not close",
)
async def get_next_steps(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> dict[str, Any]:
    """Evidence gaps as follow-up suggestions.

    These are real, computed coverage holes from the critic node — not generic
    "you might also like" filler.
    """
    job_data = await _load_job(job_id, service)
    doc = _document(job_data)
    gaps = [_gap_dict(g) for g in doc.gaps]
    gaps = [g for g in gaps if g["description"]]
    gaps.sort(key=lambda g: g["priority"], reverse=True)
    return {"gaps": gaps}


def _gap_dict(gap: Any) -> dict[str, Any]:
    """Normalise a gap to a dict.

    Gaps arrive as either ``EvidenceGap`` objects (live pipeline) or plain
    dicts (after a ``report_document`` round-trip through Redis), so both
    shapes must be read.
    """
    if isinstance(gap, dict):
        return {
            "description": str(gap.get("description", "") or "")[:300],
            "target_query": str(gap.get("target_query", "") or "")[:300],
            "priority": _safe_int(gap.get("priority", 1)),
        }
    return {
        "description": str(getattr(gap, "description", "") or "")[:300],
        "target_query": str(getattr(gap, "target_query", "") or "")[:300],
        "priority": _safe_int(getattr(gap, "priority", 1)),
    }


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@router.get(
    "/{job_id}/trust",
    summary="Plain-language summary of how well supported the report is",
)
async def get_trust(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> dict[str, Any]:
    """One line a reader can calibrate trust from.

    Counts real claim statuses. A contested or refuted claim is always named —
    the line exists so the reader can judge, and hiding problems defeats it.

    The measured evidence-quality metrics (citation coverage, hallucination
    rate) are included when the run produced them. They are omitted rather
    than defaulted when they were not measured, so a reader can tell the
    difference between "0% coverage" and "not measured".
    """
    job_data = await _load_job(job_id, service)
    doc = _document(job_data)
    summary = trust_summary(doc)
    result: dict[str, Any] = {"summary": summary, "headline": trust_headline(summary)}
    evaluation = job_data.get("evaluation_stats") or {}
    if evaluation:
        result["evaluation"] = evaluation
    return result


@router.get(
    "/{job_id}/study-guide",
    summary="Study questions generated from the report's own content",
)
async def get_study_guide(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> dict[str, Any]:
    """Study questions drawn only from the report's blocks.

    Returns an empty list with a note when there is nothing usable — never
    fabricated questions.
    """
    job_data = await _load_job(job_id, service)
    doc = _document(job_data)
    guide = await build_study_guide(doc, router=service.llm_router)
    return guide.to_dict()


@router.get(
    "/{job_id}/faq",
    summary="FAQ generated from the report's own content",
)
async def get_faq(
    job_id: str,
    service: ResearchApplicationService = Depends(_get_service),
) -> dict[str, Any]:
    """FAQ drawn only from the report's blocks. Same honesty contract."""
    job_data = await _load_job(job_id, service)
    doc = _document(job_data)
    guide = await build_faq(doc, router=service.llm_router)
    return guide.to_dict()
