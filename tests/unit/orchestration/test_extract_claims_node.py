"""
Unit tests for extract_claims_node.

Uses a stub LLMRouter so no real LLM call is made. Verifies JSON parsing,
Claim/Citation mapping, empty-input handling, and malformed-output tolerance.
"""

from __future__ import annotations

from typing import Any

import pytest

from searchops.orchestration.evidence import Citation
from searchops.orchestration.nodes.extract_claims import (
    _extract_from_doc,
    extract_claims_node,
)


class StubLLMRouter:
    """Minimal stand-in for LLMRouter that returns a canned claim JSON."""

    def __init__(self, response: str = "") -> None:
        self.response = response

    async def generate(self, prompt: str, *, system_prompt: str | None = None, **kwargs: Any) -> str:
        return self.response


_VALID = """{"claims": [
  {"text": "Bright Data Unlocker has a 99.85% success rate.",
   "confidence": 0.9,
   "citations": [{"url": "https://example.com/bench", "title": "Bench", "date": "2026-04-12",
                  "author": "A", "excerpt": "99.85%"}]}
]}"""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_claims_parses_and_maps_citation():
    router = StubLLMRouter(_VALID)
    doc = {"url": "https://example.com/bench", "title": "Bench", "content": "some article text"}
    claims = await _extract_from_doc(router, doc)
    assert len(claims) == 1
    assert claims[0].confidence == 0.9
    assert isinstance(claims[0].citations[0], Citation)
    assert claims[0].citations[0].url == "https://example.com/bench"
    assert claims[0].citations[0].title == "Bench"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_claims_rejects_non_http_citation():
    blob = ('{"claims":[{"text":"x","confidence":0.5,'
            '"citations":[{"url":"example.com/no-scheme"}]}]}')
    router = StubLLMRouter(blob)
    claims = await _extract_from_doc(router, {"url": "u", "content": "txt"})
    assert claims[0].citations == []  # non-http dropped


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_claims_malformed_returns_empty():
    router = StubLLMRouter("this is not json")
    claims = await _extract_from_doc(router, {"url": "u", "content": "txt"})
    assert claims == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_claims_empty_content_returns_empty():
    router = StubLLMRouter(_VALID)
    claims = await _extract_from_doc(router, {"url": "u", "content": "   "})
    assert claims == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_claims_node_no_scraped_returns_empty():
    router = StubLLMRouter(_VALID)
    out = await extract_claims_node({"query": "q", "scraped_contents": []}, llm_router=router)
    assert out["evidence_claims"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_claims_node_multiple_docs_concurrent():
    router = StubLLMRouter(_VALID)
    out = await extract_claims_node(
        {"scraped_contents": [{"url": "a", "content": "x"}, {"url": "b", "content": "y"}]},
        llm_router=router,
    )
    assert len(out["evidence_claims"]) == 2