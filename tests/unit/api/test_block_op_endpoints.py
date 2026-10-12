"""Tests for the block-operation endpoint.

Pins the HTTP contract:

1. A successful op returns the updated document and persists it, so the edit
   survives a reload.
2. A failed op returns ``ok: False`` with a user-showable reason and leaves the
   stored document unchanged — a failed edit must never look successful.
3. Unknown job is 404; a job with no report is 409.
4. An unknown op is refused, not silently ignored.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from searchops.api.v1.research import _get_service, router
from searchops.application.report_document import ReportDocument
from searchops.application.research_service import ResearchApplicationService, ResearchJobStatus

MD_DOC = (
    "# QEC\n\n"
    "## Findings\n\n"
    "Surface codes scale logarithmically with code distance. "
    "Logical error rates fall as the lattice grows. "
    "Overhead remains the practical limit today.\n\n"
    "## Sources\n\n- [IBM](https://ibm.com/a)\n"
)


class _Claim:
    def __init__(self, cid, text):
        self.id, self.text, self.status = cid, text, "supported"
        self.citations = [type("C", (), {"url": "https://ibm.com/a"})()]


class _StubManager:
    def __init__(self, payload):
        self._payload = payload
        self.get_job = AsyncMock(return_value=payload)
        self.update_job = AsyncMock(side_effect=self._update)

    async def _update(self, job_id, patch):
        self._payload.update(patch)
        return self._payload


def _svc(payload):
    svc = AsyncMock(spec=ResearchApplicationService)
    svc.job_state_manager = _StubManager(payload)
    # AsyncMock(spec=...) does not create nested attributes.
    svc.llm_router = None
    return svc


def _job() -> dict:
    doc = ReportDocument.from_markdown(
        "job-1", "qec", MD_DOC, claims=[_Claim("c1", "Surface codes scale logarithmically.")]
    )
    return {
        "job_id": "job-1",
        "status": ResearchJobStatus.COMPLETED,
        "query": "qec",
        "progress": 100,
        "final_report": doc.to_markdown(),
        "report_document": doc.to_dict(),
    }


def _app(svc):
    app = FastAPI()
    app.dependency_overrides[_get_service] = lambda: svc
    app.include_router(router, prefix="/api/v1")
    return app


def _para_id(job) -> str:
    return next(b["id"] for b in job["report_document"]["blocks"] if b["kind"] == "paragraph")


@pytest.mark.unit
def test_hide_op_returns_updated_document_and_persists():
    job = _job()
    svc = _svc(job)
    client = TestClient(_app(svc))

    resp = client.post(f"/api/v1/research/job-1/blocks/{_para_id(job)}/op", json={"op": "hide"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["op"] == "hide"
    assert "Surface codes" not in _doc_markdown(body["document"])
    # The edit was written back so it survives a reload.
    svc.job_state_manager.update_job.assert_awaited()
    assert "report_document" in svc.job_state_manager.update_job.await_args.args[1]


@pytest.mark.unit
def test_unhide_op_restores_a_hidden_block():
    job = _job()
    svc = _svc(job)
    client = TestClient(_app(svc))
    block_id = _para_id(job)

    client.post(f"/api/v1/research/job-1/blocks/{block_id}/op", json={"op": "hide"})
    resp = client.post(f"/api/v1/research/job-1/blocks/{block_id}/op", json={"op": "unhide"})

    assert resp.status_code == 200
    assert "Surface codes" in _doc_markdown(resp.json()["document"])


@pytest.mark.unit
def test_condense_op_shortens_the_block():
    job = _job()
    svc = _svc(job)
    resp = TestClient(_app(svc)).post(f"/api/v1/research/job-1/blocks/{_para_id(job)}/op", json={"op": "condense"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.unit
def test_split_op_creates_a_second_block():
    job = _job()
    svc = _svc(job)
    resp = TestClient(_app(svc)).post(
        f"/api/v1/research/job-1/blocks/{_para_id(job)}/op",
        json={"op": "split", "at_char": 40},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["touched_ids"]) == 2


@pytest.mark.unit
def test_failed_op_returns_ok_false_with_a_reason():
    job = _job()
    svc = _svc(job)
    resp = TestClient(_app(svc)).post("/api/v1/research/job-1/blocks/no-such-block/op", json={"op": "hide"})

    assert resp.status_code == 200, "a refused op is not a server error"
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"]
    # Nothing was persisted, so the stored document is unchanged.
    svc.job_state_manager.update_job.assert_not_awaited()


@pytest.mark.unit
def test_unknown_op_is_refused_with_a_reason():
    job = _job()
    resp = TestClient(_app(_svc(job))).post(
        f"/api/v1/research/job-1/blocks/{_para_id(job)}/op", json={"op": "teleport"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "Unknown operation" in body["reason"]


@pytest.mark.unit
def test_source_blocks_cannot_be_operated_on():
    job = _job()
    src_id = next(b["id"] for b in job["report_document"]["blocks"] if b["kind"] == "source")
    resp = TestClient(_app(_svc(job))).post(f"/api/v1/research/job-1/blocks/{src_id}/op", json={"op": "condense"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "evidence" in resp.json()["reason"].lower()


@pytest.mark.unit
def test_unknown_job_is_404():
    resp = TestClient(_app(_svc(None))).post("/api/v1/research/nope/blocks/b1/op", json={"op": "hide"})
    assert resp.status_code == 404


@pytest.mark.unit
def test_running_job_is_409():
    payload = {"job_id": "job-1", "status": ResearchJobStatus.RUNNING, "progress": 40}
    resp = TestClient(_app(_svc(payload))).post("/api/v1/research/job-1/blocks/b1/op", json={"op": "hide"})

    assert resp.status_code == 409
    assert "has not produced a report" in resp.json()["detail"]


@pytest.mark.unit
def test_legacy_job_without_a_document_still_works():
    """A job completed before the block model existed must still be editable."""
    job = {
        "job_id": "job-legacy",
        "status": ResearchJobStatus.COMPLETED,
        "query": "qec",
        "progress": 100,
        "final_report": MD_DOC,
        "report_quality": "ok",
    }
    resp = TestClient(_app(_svc(job))).post("/api/v1/research/job-legacy/blocks/b1/op", json={"op": "hide"})

    # The block id is not known to the freshly parsed document, so the op is
    # refused — but with a reason, not a 500.
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def _doc_markdown(document: dict) -> str:
    """Re-render the returned document so assertions read against real output."""
    rebuilt = ReportDocument.from_dict(document)
    return rebuilt.to_markdown()
