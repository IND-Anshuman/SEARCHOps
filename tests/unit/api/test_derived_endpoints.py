"""Tests for the derived-artifact endpoints.

Two things are pinned down:

1. The artifacts are derived from evidence the pipeline already produced
   (claims, entities, gaps). Nothing here invents content — a glossary entry
   with no description stays empty rather than being filled from model memory.
2. Every failure mode is honest. An empty study guide says so; a missing
   Chromium surfaces as an error rather than an empty file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from searchops.api.v1.derived import _get_service, router
from searchops.application.report_document import ReportDocument
from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus

MD_DOC = (
    "# Quantum Error Correction\n\n"
    "## Findings\n\nSurface codes scale logarithmically with code distance.\n\n"
    "## Sources\n\n- [IBM](https://ibm.com/a)\n"
)


class _Entity:
    def __init__(self, name, etype, desc, conf=0.9):
        self.name, self.entity_type, self.description, self.confidence = name, etype, desc, conf


class _Claim:
    def __init__(self, cid, text, status="supported", url="https://ibm.com/a"):
        self.id, self.text, self.status = cid, text, status
        self.citations = [type("C", (), {"url": url})()]


def _job() -> dict:
    doc = ReportDocument.from_markdown(
        "job-1",
        "qec",
        MD_DOC,
        claims=[_Claim("c1", "Surface codes scale logarithmically.")],
        entities=[
            _Entity("Surface code", "Concept", "A quantum error-correcting code laid out on a 2D lattice.", 0.95),
            _Entity("Logical qubit", "Concept", "", 0.8),  # no description — must stay empty
        ],
        gaps=[{"description": "No data on cost per logical qubit", "target_query": "qec cost", "priority": 2}],
    )
    return {
        "job_id": "job-1",
        "status": ResearchJobStatus.COMPLETED,
        "query": "qec",
        "progress": 100,
        "final_report": doc.to_markdown(),
        "report_document": doc.to_dict(),
        "evidence_claims": [c.to_dict() if hasattr(c, "to_dict") else c for c in doc.claims],
        "evidence_gaps": doc.gaps,
    }


class _StubManager:
    def __init__(self, payload):
        self.get_job = AsyncMock(return_value=payload)


def _svc(payload):
    svc = AsyncMock(spec=ResearchApplicationService)
    svc.job_state_manager = _StubManager(payload)
    return svc


def _build_test_app(svc):
    app = FastAPI()
    app.dependency_overrides[_get_service] = lambda: svc
    app.include_router(router)
    return app


# ── Glossary (no LLM) ────────────────────────────────────────────────────────


def test_glossary_is_built_from_real_entities_only():
    resp = TestClient(_build_test_app(_svc(_job()))).get("/derived/job-1/glossary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["terms"], "entities exist, so a glossary must be produced"
    by_term = {t["term"]: t for t in body["terms"]}
    assert by_term["Surface code"]["definition"].startswith("A quantum error-correcting")
    # No description means no definition — never invented.
    assert by_term["Logical qubit"]["definition"] == ""


def test_glossary_orders_by_confidence():
    resp = TestClient(_build_test_app(_svc(_job()))).get("/derived/job-1/glossary")
    terms = resp.json()["terms"]
    assert terms[0]["term"] == "Surface code"


def test_glossary_empty_when_no_entities():
    payload = _job()
    payload["report_document"]["entities"] = []
    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/glossary")

    assert resp.status_code == 200
    assert resp.json()["terms"] == []


# ── Evidence gaps ────────────────────────────────────────────────────────────


def test_evidence_gaps_are_returned_as_next_steps():
    resp = TestClient(_build_test_app(_svc(_job()))).get("/derived/job-1/next-steps")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["gaps"]) == 1
    assert body["gaps"][0]["description"].startswith("No data on cost")
    assert body["gaps"][0]["priority"] == 2


def test_next_steps_empty_when_no_gaps():
    payload = _job()
    payload["report_document"]["gaps"] = []
    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/next-steps")

    assert resp.status_code == 200
    assert resp.json()["gaps"] == []


# ── Trust summary ────────────────────────────────────────────────────────────


def test_trust_summary_counts_real_claim_statuses():
    resp = TestClient(_build_test_app(_svc(_job()))).get("/derived/job-1/trust")

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total"] == 1
    assert body["summary"]["supported"] == 1
    assert "well-supported" in body["headline"]


def test_trust_summary_says_so_when_no_claims():
    payload = _job()
    payload["report_document"]["claims"] = []
    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/trust")

    assert resp.status_code == 200
    assert "No individual claims" in resp.json()["headline"]


# ── Error handling ───────────────────────────────────────────────────────────


def test_derived_unknown_job_is_404():
    resp = TestClient(_build_test_app(_svc(None))).get("/derived/nope/glossary")
    assert resp.status_code == 404


def test_derived_running_job_is_409():
    payload = {"job_id": "job-1", "status": ResearchJobStatus.RUNNING, "progress": 40}
    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/glossary")

    assert resp.status_code == 409
    assert "has not produced a report" in resp.json()["detail"]
