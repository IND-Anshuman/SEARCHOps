"""
Unit tests for verifier_node.

Uses a stub LLMRouter. Verifies batching, verdict mapping, skip-when-no-
unverified, and no-claim short-circuit.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.orchestration.evidence import Claim
from searchops.orchestration.nodes.verifier import verifier_node


class StubLLMRouter:
    def __init__(self, verdicts_json: str = '{"verdicts":[]}') -> None:
        self.verdicts_json = verdicts_json
        self.call_count = 0

    async def generate(self, prompt: str, *, system_prompt: str | None = None, **kwargs: Any) -> str:
        self.call_count += 1
        return self.verdicts_json


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_skips_when_no_claims():
    router = StubLLMRouter()
    out = await verifier_node({"query": "q", "evidence_claims": []}, llm_router=router)
    assert router.call_count == 0
    assert out == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_skips_when_all_verified():
    router = StubLLMRouter()
    out = await verifier_node({"query": "q",
                               "evidence_claims": [Claim(id="c1", text="x", status="supported")]},
                              llm_router=router)
    assert router.call_count == 0
    assert out == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_applies_verdicts():
    router = StubLLMRouter('{"verdicts":[{"claim_id":"c1","verdict":"supported"},'
                           '{"claim_id":"c2","verdict":"contested"}]}')
    claims = [
        Claim(id="c1", text="claim 1"),
        Claim(id="c2", text="claim 2"),
    ]
    out = await verifier_node({"query": "q", "evidence_claims": claims}, llm_router=router)
    mapped = {c.id: c.status for c in out["evidence_claims"]}
    assert mapped["c1"] == "supported"
    assert mapped["c2"] == "contested"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_batching_one_call_per_8():
    # 16 unverified claims → should be 2 batches → 2 LLM calls
    verdicts = {"verdicts": []}
    for i in range(16):
        verdicts["verdicts"].append({"claim_id": f"c{i}", "verdict": "supported"})
    import json
    router = StubLLMRouter(json.dumps(verdicts))
    claims = [Claim(id=f"c{i}", text=f"claim {i}") for i in range(16)]
    await verifier_node({"query": "q", "evidence_claims": claims}, llm_router=router)
    assert router.call_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_malformed_output_no_crash():
    router = StubLLMRouter("not json at all")
    claims = [Claim(id="c1", text="x")]
    out = await verifier_node({"query": "q", "evidence_claims": claims}, llm_router=router)
    # claims unchanged (default unverified)
    assert out["evidence_claims"][0].status == "unverified"