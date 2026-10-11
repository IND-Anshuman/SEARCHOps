"""Regression tests: the block document must reach the API response.

JobStatusResponse sets ``extra="ignore"``, so any field the service writes but
the schema does not declare is silently stripped before the response. That is
exactly how ``report_quality`` and ``report_user_message`` were lost before.
The block document must be declared for the same reason, or every block-level
feature (export, where-to-go-next, study guide, trust header) has nothing to
act on client-side.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from searchops.api.v1.research import _get_service, router
from searchops.application.report_document import ReportDocument
from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus

MD_DOC = "# Title\n\n## Findings\n\nSurface codes scale logarithmically.\n\n## Sources\n\n- [A](https://a.com)\n"


def _build_test_app(mock_service: ResearchApplicationService) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.mark.unit
def test_get_research_status_exposes_report_document():
    doc = ReportDocument.from_markdown("job-abc-123", "qec", MD_DOC)
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": ResearchJobStatus.COMPLETED,
        "progress": 100,
        "final_report": doc.to_markdown(),
        "report_document": doc.to_dict(),
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["report_document"] is not None
    blocks = body["report_document"]["blocks"]
    assert len(blocks) >= 3
    kinds = {b["kind"] for b in blocks}
    assert "source" in kinds, "citation blocks must be marked immutable"


@pytest.mark.unit
def test_get_research_status_report_document_defaults_to_none():
    """A running job has no document yet; it must not fabricate one."""
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": ResearchJobStatus.RUNNING,
        "progress": 30,
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123")

    assert resp.status_code == 200
    assert resp.json()["report_document"] is None
