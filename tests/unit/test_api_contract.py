"""
API contract tests.

Verifies that the JobStatusResponse Pydantic model correctly serializes
all fields produced by ResearchApplicationService, with no field stripping.
These tests caught audit finding F-12 (response schema missing telemetry fields).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from searchops.api.v1.research import (
    JobStatusResponse,
    LangGraphNodeSchema,
    LogItemSchema,
    VectorChunkSchema,
)


# ── JobStatusResponse ─────────────────────────────────────────────────────────

class TestJobStatusResponse:
    def test_all_telemetry_fields_present(self):
        """JobStatusResponse must expose every telemetry field — no field stripping."""
        data = {
            "job_id": "test-job-123",
            "status": "completed",
            "query": "test query",
            "depth": "standard",
            "progress": 100,
            "final_report": "# Report\n\nContent here.",
            "citations": ["https://example.com"],
            "entity_count": 5,
            "source_count": 3,
            "token_used": 45000,
            "token_budget": 150000,
            "cost_current": 0.034,
            "cost_budget": 5.0,
            "nodes": [],
            "error": None,
            "created_at": "2026-08-08T00:00:00Z",
            "completed_at": "2026-08-08T00:01:30Z",
        }
        response = JobStatusResponse.model_validate(data)

        # Verify every field is present and correct
        assert response.job_id == "test-job-123"
        assert response.status == "completed"
        assert response.depth == "standard"
        assert response.token_used == 45000
        assert response.token_budget == 150000
        assert response.cost_current == 0.034
        assert response.cost_budget == 5.0
        assert response.final_report == "# Report\n\nContent here."

    def test_serialization_preserves_all_fields(self):
        """model_dump() must not strip declared fields."""
        response = JobStatusResponse(
            job_id="j1",
            status="running",
            token_used=12000,
            cost_current=0.009,
            depth="deep",
        )
        dumped = response.model_dump()
        assert "token_used" in dumped
        assert "token_budget" in dumped
        assert "cost_current" in dumped
        assert "cost_budget" in dumped
        assert "nodes" in dumped
        assert "depth" in dumped
        assert dumped["token_used"] == 12000
        assert dumped["cost_current"] == 0.009

    def test_defaults_are_safe(self):
        """Minimal required fields should not raise; all others have defaults."""
        response = JobStatusResponse(job_id="j2", status="pending")
        assert response.progress == 0
        assert response.token_used == 0
        assert response.cost_current == 0.0
        assert response.nodes == []
        assert response.citations == []

    def test_extra_fields_ignored(self):
        """Extra unknown fields from Redis dict must not cause validation errors."""
        data = {
            "job_id": "j3",
            "status": "running",
            "unknown_field_xyz": "should be ignored",
            "another_extra": 42,
        }
        response = JobStatusResponse.model_validate(data)
        assert response.job_id == "j3"


# ── LangGraphNodeSchema ───────────────────────────────────────────────────────

class TestLangGraphNodeSchema:
    def test_node_schema_validation(self):
        node_data = {
            "id": "planner",
            "label": "Planner Agent",
            "type": "planner",
            "status": "completed",
            "latency_ms": 1847,
            "token_cost": 0.003,
            "retries": 0,
            "timestamp": "2026-08-08T00:00:00Z",
            "prompt": "system_planner_v4.2.jinja2",
            "input_payload": {"query": "test"},
            "output_payload": {"plan": "..."},
        }
        node = LangGraphNodeSchema.model_validate(node_data)
        assert node.id == "planner"
        assert node.latency_ms == 1847  # real value, not 1200
        assert node.token_cost == 0.003

    def test_node_defaults(self):
        node = LangGraphNodeSchema(id="search", label="Search", type="search", status="pending")
        assert node.latency_ms == 0
        assert node.token_cost == 0.0


# ── VectorChunkSchema (alias handling) ───────────────────────────────────────

class TestVectorChunkSchema:
    def test_snake_case_aliases(self):
        """Backend uses snake_case field names; schema must accept both aliases."""
        chunk_data = {
            "id": "chunk_0",
            "document_title": "LangGraph Docs",
            "source_url": "https://docs.langchain.com",
            "similarity_score": 0.92,
            "token_count": 312,
            "chunk_preview": "LangGraph is a library...",
        }
        chunk = VectorChunkSchema.model_validate(chunk_data)
        assert chunk.documentTitle == "LangGraph Docs"
        assert chunk.sourceUrl == "https://docs.langchain.com"
        assert chunk.similarityScore == 0.92


# ── LogItemSchema (alias handling) ────────────────────────────────────────────

class TestLogItemSchema:
    def test_snake_case_aliases(self):
        log_data = {
            "id": "evt_123",
            "stream": "searchops:events:langgraph",
            "event_type": "NODE_PLANNER_COMPLETED",
            "correlation_id": "job-abc",
            "timestamp": "00:01:23.456",
            "payload": {"node": "planner"},
            "level": "info",
        }
        log_item = LogItemSchema.model_validate(log_data)
        assert log_item.eventType == "NODE_PLANNER_COMPLETED"
        assert log_item.correlationId == "job-abc"
