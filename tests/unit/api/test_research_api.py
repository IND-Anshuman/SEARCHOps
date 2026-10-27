"""
Unit tests for Research REST API endpoints (Phase 9).

Uses FastAPI's TestClient and mocked ResearchApplicationService
to avoid touching real Redis / LangGraph dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from searchops.api.v1.research import _get_service, router
from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus
from searchops.core.context.research import ReportType

# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_test_app(mock_service: ResearchApplicationService) -> FastAPI:
    """Build a minimal FastAPI app with the research router and injected mock."""
    app = FastAPI()

    # Override the dependency
    app.dependency_overrides[_get_service] = lambda: mock_service
    app.include_router(router, prefix="/api/v1")
    return app


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_start_research_returns_202():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.start_research.return_value = "job-abc-123"

    client = TestClient(_build_test_app(mock_svc), raise_server_exceptions=True)
    resp = client.post(
        "/api/v1/research/",
        json={
            "query": "What is quantum computing?",
            "depth": "standard",
            "max_sources": 5,
        },
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "job-abc-123"
    assert body["status"] == ResearchJobStatus.PENDING
    mock_svc.start_research.assert_called_once_with(
        query="What is quantum computing?",
        depth="standard",
        max_sources=5,
        mode="precision",
        # config=None means "use mode defaults" — scraping configuration is
        # user-tunable now, and omitting it keeps the previous behaviour.
        config=None,
        # Report shape defaults to auto, which the writer resolves from the
        # query at generation time.
        report_type=ReportType.AUTO,
        report_structure_prompt="",
        target_pages=12,
        report_theme="default",
        owner_id="",  # API-key (service) caller: no Clerk user owns the job
    )


@pytest.mark.unit
def test_get_research_status_found():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": ResearchJobStatus.COMPLETED,
        "query": "What is quantum computing?",
        "progress": 100,
        "final_report": "# Report\n\nQuantum computing uses qubits.",
        "citations": ["https://example.com"],
        "entity_count": 3,
        "source_count": 5,
        "error": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:05:00+00:00",
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == ResearchJobStatus.COMPLETED
    assert body["entity_count"] == 3
    assert "qubits" in body["final_report"]


@pytest.mark.unit
def test_get_research_status_exposes_report_provenance():
    """Regression: report_quality / report_user_message were computed by
    report_writer and stored in state, but JobStatusResponse never declared
    them, so pydantic silently stripped them before the response. A degraded
    (synthesised) report reached the UI looking like a normal model report.
    """
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": ResearchJobStatus.COMPLETED,
        "query": "qec",
        "progress": 100,
        "final_report": "# Research Report: qec  \n\n2 sources were retrieved...",
        "report_quality": "refusal",
        "report_user_message": (
            "The model declined to summarise this query, so this report was built from the sources that were found."
        ),
        "citations": ["https://a.com", "https://b.com"],
        "entity_count": 0,
        "source_count": 2,
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["report_quality"] == "refusal"
    assert "declined" in body["report_user_message"]


@pytest.mark.unit
def test_get_research_status_exposes_evidence_gaps_and_claims():
    """The critic emits typed EvidenceGap objects; they must reach the client
    so the UI can show what the run could not establish."""
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": ResearchJobStatus.COMPLETED,
        "progress": 100,
        "evidence_gaps": [
            {"description": "No data on cost per logical qubit", "target_query": "qec cost", "priority": 2}
        ],
        "evidence_claims": [
            {
                "id": "c1",
                "text": "Surface codes scale logarithmically.",
                "status": "supported",
                "confidence": 0.9,
                "citations": [{"url": "https://a.com"}],
            }
        ],
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["evidence_gaps"]) == 1
    assert body["evidence_gaps"][0]["description"].startswith("No data on cost")
    assert len(body["evidence_claims"]) == 1
    assert body["evidence_claims"][0]["status"] == "supported"


@pytest.mark.unit
def test_get_research_status_provenance_defaults_to_none():
    """A job that never reached the report writer must not fabricate a quality."""
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": ResearchJobStatus.RUNNING,
        "progress": 40,
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["report_quality"] is None
    assert body["evidence_gaps"] == []
    assert body["evidence_claims"] == []


@pytest.mark.unit
def test_get_research_status_not_found():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = None

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/nonexistent-id")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.unit
def test_delete_research_job():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    # New architecture: DELETE calls service.job_state_manager.delete_job()
    mock_svc.job_state_manager = AsyncMock()
    mock_svc.job_state_manager.delete_job = AsyncMock(return_value=None)

    client = TestClient(_build_test_app(mock_svc))
    resp = client.delete("/api/v1/research/job-abc-123")

    assert resp.status_code == 204
    mock_svc.job_state_manager.delete_job.assert_called_once_with("job-abc-123")


@pytest.mark.unit
def test_start_research_validation_error():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    client = TestClient(_build_test_app(mock_svc), raise_server_exceptions=False)

    # Query too short (min_length=3)
    resp = client.post("/api/v1/research/", json={"query": "AI"})
    assert resp.status_code == 422


@pytest.mark.unit
def test_get_research_graph():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": "completed",
        "entities": [
            {
                "id": "e1",
                "canonical_id": "E1",
                "name": "N1",
                "entity_type": "concept",
                "description": "D1",
                "confidence": 0.9,
            }
        ],
        "relations": [
            {
                "id": "r1",
                "source_id": "e1",
                "target_id": "e1",
                "source_canonical_id": "E1",
                "target_canonical_id": "E1",
                "relation_type": "TYPE",
                "description": "DESC",
            }
        ],
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["name"] == "N1"
    assert len(body["edges"]) == 1
    assert body["edges"][0]["relation_type"] == "TYPE"


@pytest.mark.unit
def test_get_research_graph_reads_the_field_names_the_pipeline_writes():
    """Regression: the producer writes type/summary, not entity_type/description.

    The graph endpoint used to read entity_type/description, so against a real
    job every node degraded to the literal placeholder type "technology" with
    an empty summary. The fixture above carries the invented key names, which
    is why it stayed green. This feeds the dicts research_service.py builds.
    """
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": "completed",
        "entities": [
            {
                "id": "e1",
                "canonical_id": "concept:transformer",
                "name": "Transformer",
                "type": "Concept",
                "summary": "A neural architecture using self-attention.",
                "confidence_score": 0.95,
            }
        ],
        "relations": [],
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123/graph")
    assert resp.status_code == 200
    node = resp.json()["nodes"][0]
    assert node["name"] == "Transformer"
    assert node["type"] == "Concept"
    assert node["summary"] == "A neural architecture using self-attention."


@pytest.mark.unit
def test_get_research_chunks():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": "completed",
        "chunks": [
            {
                "id": "c1",
                "documentTitle": "T1",
                "sourceUrl": "U1",
                "similarityScore": 0.95,
                "tokenCount": 100,
                "chunkPreview": "P1",
            }
        ],
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["chunks"]) == 1
    assert body["chunks"][0]["documentTitle"] == "T1"


@pytest.mark.unit
def test_get_research_logs():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": "completed",
        "logs": [
            {
                "id": "l1",
                "stream": "s1",
                "eventType": "t1",
                "correlationId": "job-abc-123",
                "timestamp": "12:00",
                "payload": {},
                "level": "info",
            }
        ],
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["logs"]) == 1
    assert body["logs"][0]["eventType"] == "t1"


@pytest.mark.unit
def test_start_research_accepts_corpus_mode() -> None:
    """A corpus request must reach the service with its mode preserved."""
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.start_research.return_value = "job-corpus-1"

    client = TestClient(_build_test_app(mock_svc), raise_server_exceptions=True)
    resp = client.post(
        "/api/v1/research/",
        json={
            "query": "Build a dataset of vector database benchmarks",
            "depth": "deep",
            "mode": "corpus",
            "max_sources": 40,
        },
    )

    assert resp.status_code == 202
    mock_svc.start_research.assert_called_once_with(
        query="Build a dataset of vector database benchmarks",
        depth="deep",
        max_sources=40,
        mode="corpus",
        config=None,
        report_type=ReportType.AUTO,
        report_structure_prompt="",
        target_pages=12,
        report_theme="default",
        owner_id="",
    )


@pytest.mark.unit
def test_start_research_rejects_unknown_mode() -> None:
    """An unknown mode is a clear 422, never a silently coerced run."""
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    client = TestClient(_build_test_app(mock_svc), raise_server_exceptions=True)

    resp = client.post(
        "/api/v1/research/",
        json={"query": "What is quantum computing?", "mode": "exhaustive"},
    )

    assert resp.status_code == 422
    # The service was never invoked — no job was created for a bogus mode.
    mock_svc.start_research.assert_not_called()


@pytest.mark.unit
def test_start_research_forwards_user_config():
    """A knob that validates but is dropped is a lie in the UI."""
    from searchops.core.context.research import ResearchConfig

    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.start_research.return_value = "job-cfg-1"

    client = TestClient(_build_test_app(mock_svc))
    resp = client.post(
        "/api/v1/research/",
        json={
            "query": "quantum error correction",
            "config": {"max_pages": 25, "max_depth": 2, "verify_sampling_rate": 0.5},
        },
    )

    assert resp.status_code == 202
    passed = mock_svc.start_research.call_args.kwargs["config"]
    assert isinstance(passed, ResearchConfig)
    assert passed.max_pages == 25
    assert passed.max_depth == 2
    assert passed.verify_sampling_rate == 0.5


@pytest.mark.unit
def test_start_research_rejects_an_inconsistent_config():
    """More pages than the scrape budget allows is refused, not silently truncated."""
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    client = TestClient(_build_test_app(mock_svc), raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/research/",
        json={
            "query": "quantum error correction",
            "config": {"max_pages": 100, "scrape_budget": 5},
        },
    )

    assert resp.status_code == 422
    detail = str(resp.json())
    assert "scrape_budget" in detail
    mock_svc.start_research.assert_not_called()


@pytest.mark.unit
def test_start_research_rejects_out_of_bounds_config():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    client = TestClient(_build_test_app(mock_svc), raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/research/",
        json={"query": "quantum error correction", "config": {"max_pages": 999_999}},
    )

    assert resp.status_code == 422
    mock_svc.start_research.assert_not_called()
