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


def test_glossary_reads_the_field_names_the_pipeline_actually_writes():
    """Regression: the producer writes type/summary/confidence_score.

    The glossary used to read entity_type/description/confidence, so against a
    real job every term came back with an empty type and an empty definition.
    The object-shaped fixture above cannot catch that — it carries the invented
    attribute names. This test feeds the dicts research_service.py builds.
    """
    payload = _job()
    payload["report_document"]["entities"] = [
        {
            "id": "e1",
            "canonical_id": "concept:surface-code",
            "name": "Surface code",
            "type": "Concept",
            "summary": "A quantum error-correcting code laid out on a 2D lattice.",
            "confidence_score": 0.95,
        },
        {
            "id": "e2",
            "canonical_id": "technology:gpu",
            "name": "GPU",
            "type": "Technology",
            "summary": "",
            "confidence_score": 0.80,
        },
    ]
    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/glossary")

    assert resp.status_code == 200
    by_term = {t["term"]: t for t in resp.json()["terms"]}
    assert by_term["Surface code"]["entity_type"] == "Concept"
    assert by_term["Surface code"]["definition"].startswith("A quantum error-correcting")
    # A real entity with no description must stay empty, not be invented.
    assert by_term["GPU"]["entity_type"] == "Technology"
    assert by_term["GPU"]["definition"] == ""


def test_glossary_survives_the_redis_round_trip():
    """Regression: to_dict() must emit plain dicts, not live pydantic models.

    JobStateManager serialises with orjson, which cannot encode a Claim or an
    EvidenceGap. Before the fix the persisted state either failed to commit or
    lost the gap and claim lists, so "Where to go next" reported zero gaps
    after the critic had emitted them.
    """
    import orjson

    payload = _job()
    raw = payload["report_document"]
    # The exact step the real path performs before storing in Redis.
    stored = orjson.loads(orjson.dumps(raw))
    payload["report_document"] = stored

    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/next-steps")
    assert resp.status_code == 200
    gaps = resp.json()["gaps"]
    assert len(gaps) == 1
    assert gaps[0]["description"].startswith("No data on cost")
    assert gaps[0]["priority"] == 2


def test_trust_counts_claims_after_the_redis_round_trip():
    """Regression: claim statuses must survive serialisation."""
    import orjson

    payload = _job()
    payload["report_document"] = orjson.loads(orjson.dumps(payload["report_document"]))

    resp = TestClient(_build_test_app(_svc(payload))).get("/derived/job-1/trust")
    assert resp.status_code == 200
    assert resp.json()["summary"]["supported"] == 1
    assert "verified against sources" in resp.json()["headline"]


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
    assert "verified against sources" in body["headline"]


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
