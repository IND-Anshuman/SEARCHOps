"""
Unit tests for critic_node.

Uses a stub LLMRouter. Verifies gap emission on empty evidence, gap parsing,
claim-status classification propagation, malformed-output tolerance, and the
no-gaps path.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.orchestration.evidence import Claim
from searchops.orchestration.nodes.critic import critic_node


class StubLLMRouter:
    def __init__(self, response: str = "") -> None:
        self.response = response

    async def generate(self, prompt: str, *, system_prompt: str | None = None, **kwargs: Any) -> str:
        return self.response


_GAPS_JSON = """{"gaps":[{"description":"Need Bright Data pricing details","target_query":"Bright Data Unlocker pricing 2026","priority":1}],
                "classifications":{"c1":"supported"}}"""

_NO_GAPS_JSON = '{"gaps":[],"classifications":{"c1":"supported"}}'

_MALFORMED = "nope, not json"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_no_claims_emits_initial_gap():
    out = await critic_node({"query": "Bright Data pricing"}, llm_router=StubLLMRouter())
    assert len(out["evidence_gaps"]) == 1
    assert "search" in out["evidence_gaps"][0].description.lower() or \
           out["evidence_gaps"][0].target_query == "Bright Data pricing"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_parses_gaps_and_classifications():
    router = StubLLMRouter(_GAPS_JSON)
    state = {"query": "Bright Data pricing 2026",
             "evidence_claims": [Claim(id="c1", text="BD cost 99.85%")]}
    out = await critic_node(state, llm_router=router)
    assert len(out["evidence_gaps"]) == 1
    assert out["evidence_gaps"][0].target_query == "Bright Data Unlocker pricing 2026"
    assert out["evidence_claims"][0].status == "supported"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_malformed_returns_no_gaps():
    router = StubLLMRouter(_MALFORMED)
    state = {"query": "x", "evidence_claims": [Claim(id="c1", text="x")]}
    out = await critic_node(state, llm_router=router)
    assert out["evidence_gaps"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_sufficient_evidence_no_gaps():
    router = StubLLMRouter(_NO_GAPS_JSON)
    out = await critic_node({"query": "q", "evidence_claims": [Claim(id="c1", text="x")]}, llm_router=router)
    assert out["evidence_gaps"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_invalid_classification_ignored():
    router = StubLLMRouter('{"gaps":[],"classifications":{"c1":"bogus_status"}}')
    out = await critic_node({"query": "q", "evidence_claims": [Claim(id="c1", text="x")]}, llm_router=router)
    # invalid status must not overwrite default "unverified"
    assert out["evidence_claims"][0].status == "unverified"