"""
Unit tests for verifier_node.

Uses a stub LLMRouter. Verifies batching, verdict mapping, skip-when-no-
unverified, and no-claim short-circuit.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.orchestration.evidence import Citation, Claim
from searchops.orchestration.nodes.verifier import EvidenceItem, decide, verifier_node
from searchops.orchestration.passages import Passage


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
    # No LLM call is made, and the sampling telemetry says nothing was verified.
    assert out["verification_stats"]["verified"] == 0
    assert out["verification_stats"]["total_claims"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_skips_when_all_verified():
    router = StubLLMRouter()
    out = await verifier_node(
        {"query": "q", "evidence_claims": [Claim(id="c1", text="x", status="supported")]},
        llm_router=router,
    )
    assert router.call_count == 0
    # Already-verified claims are not re-verified.
    assert out["verification_stats"]["verified"] == 0
    assert out["verification_stats"]["unverified"] == 0


def _cited(cid: str, text: str, url: str, pid: str) -> Claim:
    return Claim(
        id=cid,
        text=text,
        citations=[Citation(url=url, excerpt=text, passage_id=pid, quote_verified=True)],
    )


_PASSAGES = [
    Passage(id="pa", url="https://a.com", text="Qdrant is written in Rust and was released in 2021."),
    Passage(id="pb", url="https://b.com", text="The Qdrant vector database, written in Rust, launched in 2021."),
    Passage(id="pc", url="https://c.com", text="Qdrant was released in 2019 according to this page about Qdrant."),
]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_checks_claims_against_other_sources():
    """Evidence includes the claim's own passage AND passages from other pages."""
    router = StubLLMRouter('{"results":[{"claim_id":"c1","labels":{"E1":"supports","E2":"supports"}}]}')
    claim = _cited("c1", "Qdrant is written in Rust and was released in 2021.", "https://a.com", "pa")
    out = await verifier_node({"query": "q", "evidence_claims": [claim], "passages": _PASSAGES}, llm_router=router)
    verified = out["evidence_claims"][0]
    assert verified.status == "supported"
    assert "https://a.com" in verified.supporting_urls
    assert len(verified.supporting_urls) == 2
    assert out["verification_stats"]["multi_source_supported"] == 1


@pytest.mark.unit
def test_verdict_is_computed_from_labels_not_asserted():
    ev = [
        EvidenceItem("E1", "https://a.com", "x", own=True),
        EvidenceItem("E2", "https://c.com", "y"),
    ]
    assert decide({"E1": "supports"}, ev).status == "supported"
    assert decide({"E1": "supports", "E2": "contradicts"}, ev).status == "contested"
    assert decide({"E2": "contradicts"}, ev).status == "refuted"
    nothing = decide({"E1": "irrelevant"}, ev)
    assert nothing.status == "contested" and nothing.note


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_batches_claims():
    # 13 claims at 6 per batch → 3 LLM calls.
    router = StubLLMRouter('{"results":[]}')
    claims = [_cited(f"c{i}", f"claim {i} about rust", "https://a.com", "pa") for i in range(13)]
    await verifier_node({"query": "q", "evidence_claims": claims, "passages": _PASSAGES}, llm_router=router)
    assert router.call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_malformed_output_no_crash():
    router = StubLLMRouter("not json at all")
    claims = [_cited("c1", "Qdrant is written in Rust.", "https://a.com", "pa")]
    out = await verifier_node({"query": "q", "evidence_claims": claims, "passages": _PASSAGES}, llm_router=router)
    # No parsable labels: the claim stays unverified rather than being guessed.
    assert out["evidence_claims"][0].status == "unverified"
