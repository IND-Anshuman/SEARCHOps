"""Tests for the report export endpoints.

These exercise the HTTP contract rather than the exporters themselves (which
have their own tests that open the produced files):

1. A completed job returns a real file with the right media type.
2. A missing job is 404, not a 500.
3. A job with no report yet is 409 — the user needs to know to wait, not to
   see a server error.
4. An export failure surfaces as an error, never as an empty 200. An export
   that silently returns nothing is discovered only after the file is sent.
5. A job completed before the block model existed still exports, by rebuilding
   the document from ``final_report``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from searchops.api.v1.research import _get_service, router
from searchops.application.report_document import ReportDocument
from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus

MD_DOC = "# QEC\n\n## Findings\n\nSurface codes scale logarithmically.\n\n## Sources\n\n- [IBM](https://ibm.com/a)\n"


class _StubManager:
    """Minimal JobStateManager stand-in.

    AsyncMock(spec=...) does not create nested attributes, so
    job_state_manager has to be attached explicitly.
    """

    def __init__(self, payload) -> None:
        self.get_job = AsyncMock(return_value=payload)


def _svc(payload) -> AsyncMock:
    svc = AsyncMock(spec=ResearchApplicationService)
    svc.job_state_manager = _StubManager(payload)
    return svc


def _build_test_app(mock_service) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.include_router(router, prefix="/api/v1")
    return app


def _job_with_document() -> dict:
    doc = ReportDocument.from_markdown("job-1", "qec", MD_DOC)
    return {
        "job_id": "job-1",
        "status": ResearchJobStatus.COMPLETED,
        "query": "qec",
        "progress": 100,
        "final_report": doc.to_markdown(),
        "report_document": doc.to_dict(),
    }


def _job_without_document() -> dict:
    """A job completed before the block model existed."""
    return {
        "job_id": "job-legacy",
        "status": ResearchJobStatus.COMPLETED,
        "query": "qec",
        "progress": 100,
        "final_report": MD_DOC,
        "report_quality": "ok",
    }


@pytest.mark.unit
def test_export_deck_returns_a_pptx():
    svc = _svc(_job_with_document())

    resp = TestClient(_build_test_app(svc)).get("/api/v1/research/job-1/export/deck")

    assert resp.status_code == 200
    assert "presentationml.presentation" in resp.headers["content-type"]
    assert resp.content[:2] == b"PK", "a .pptx is a zip container"


@pytest.mark.unit
def test_export_docx_returns_a_docx():
    svc = _svc(_job_with_document())

    resp = TestClient(_build_test_app(svc)).get("/api/v1/research/job-1/export/docx")

    assert resp.status_code == 200
    assert "wordprocessingml.document" in resp.headers["content-type"]
    assert resp.content[:2] == b"PK"


@pytest.mark.unit
def test_export_deck_for_legacy_job_without_document():
    """A job that finished before the block model existed must still export."""
    svc = _svc(_job_without_document())

    resp = TestClient(_build_test_app(svc)).get("/api/v1/research/job-legacy/export/deck")

    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"


@pytest.mark.unit
def test_export_unknown_job_is_404():
    svc = _svc(None)

    resp = TestClient(_build_test_app(svc)).get("/api/v1/research/nope/export/deck")

    assert resp.status_code == 404


@pytest.mark.unit
def test_export_running_job_is_409():
    """The user needs to be told to wait, not shown a server error."""
    svc = _svc({"job_id": "job-1", "status": ResearchJobStatus.RUNNING, "progress": 40})

    resp = TestClient(_build_test_app(svc)).get("/api/v1/research/job-1/export/deck")

    assert resp.status_code == 409
    assert "has not produced a report" in resp.json()["detail"]


@pytest.mark.unit
def test_export_failure_is_not_an_empty_200(monkeypatch):
    """An exporter that raises must surface as an error. A silent empty 200 is
    discovered only after the file has been forwarded."""
    import searchops.api.v1.research as mod

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mod, "export_deck", _boom)
    svc = _svc(_job_with_document())

    resp = TestClient(_build_test_app(svc)).get("/api/v1/research/job-1/export/deck")

    assert resp.status_code == 500
    assert "disk full" in resp.json()["detail"]


@pytest.mark.unit
def test_pdf_without_a_server_renderer_tells_the_browser_to_print(monkeypatch) -> None:
    """API-only deployment: no Playwright, so the web app prints the page itself.

    The 503 comes before the study guide is built — that is an LLM call, and
    without a renderer there is nothing to spend it on.
    """
    import searchops.api.v1.research as research_api

    monkeypatch.setattr(research_api, "pdf_export_available", lambda: False)
    guide = AsyncMock()
    monkeypatch.setattr(research_api, "_study_guide_for_export", guide)
    client = TestClient(_build_test_app(_svc(_job_with_document())))

    res = client.get("/api/v1/research/job-1/export/pdf")

    assert res.status_code == 503
    assert res.headers["x-pdf-fallback"] == "browser-print"
    assert "print" in res.json()["detail"]
    guide.assert_not_called()
