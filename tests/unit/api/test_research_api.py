"""
Unit tests for Research REST API endpoints (Phase 9).

Uses FastAPI's TestClient and mocked ResearchApplicationService
to avoid touching real Redis / LangGraph dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from searchops.api.v1.research import router, StartResearchRequest, _get_service
from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus
from fastapi import FastAPI


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
    resp = client.post("/api/v1/research/", json={
        "query": "What is quantum computing?",
        "depth": "standard",
        "max_sources": 5,
    })

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "job-abc-123"
    assert body["status"] == ResearchJobStatus.PENDING
    mock_svc.start_research.assert_called_once_with(
        query="What is quantum computing?",
        depth="standard",
        max_sources=5,
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
        "entities": [{"id": "e1", "canonical_id": "E1", "name": "N1", "entity_type": "concept", "description": "D1", "confidence": 0.9}],
        "relations": [{"id": "r1", "source_id": "e1", "target_id": "e1", "source_canonical_id": "E1", "target_canonical_id": "E1", "relation_type": "TYPE", "description": "DESC"}]
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
def test_get_research_chunks():
    mock_svc = AsyncMock(spec=ResearchApplicationService)
    mock_svc.get_job_status.return_value = {
        "job_id": "job-abc-123",
        "status": "completed",
        "chunks": [{"id": "c1", "documentTitle": "T1", "sourceUrl": "U1", "similarityScore": 0.95, "tokenCount": 100, "chunkPreview": "P1"}]
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
        "logs": [{"id": "l1", "stream": "s1", "eventType": "t1", "correlationId": "job-abc-123", "timestamp": "12:00", "payload": {}, "level": "info"}]
    }

    client = TestClient(_build_test_app(mock_svc))
    resp = client.get("/api/v1/research/job-abc-123/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["logs"]) == 1
    assert body["logs"][0]["eventType"] == "t1"
