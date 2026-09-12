"""
Unit tests for the typed evidence layer (Claim / Citation / EvidenceGap /
EvidenceStore) and the ``dedupe_claims`` LangGraph state reducer.
"""

from __future__ import annotations

import pytest

from searchops.orchestration.evidence import (
    Citation,
    Claim,
    EvidenceGap,
    EvidenceSpan,
    EvidenceStore,
    dedupe_claims,
)


@pytest.mark.unit
def test_claim_citation_roundtrip():
    """Claim → JSON → Claim preserves id, url, excerpt."""
    c = Claim(
        id="c1",
        text="Bright Data Unlocker has a 99.85% success rate",
        confidence=0.9,
        citations=[
            Citation(
                url="https://example.com/bench",
                title="Benchmark",
                date="2026-04-12",
                author="A. Test",
                excerpt="99.85% success",
            )
        ],
        evidence=[EvidenceSpan(doc_id="d1", start=0, end=42)],
    )
    j = c.model_dump_json()
    c2 = Claim.model_validate_json(j)
    assert c2.id == "c1"
    assert c2.citations[0].url == "https://example.com/bench"
    assert c2.citations[0].excerpt == "99.85% success"
    assert c2.evidence[0].doc_id == "d1"


@pytest.mark.unit
def test_claim_default_status():
    """Claim defaults to unverified status and 0.5 confidence."""
    c = Claim(id="c1", text="something")
    assert c.status == "unverified"
    assert c.confidence == 0.5
    assert c.citations == []


@pytest.mark.unit
def test_evidence_gap_roundtrip():
    g = EvidenceGap(description="Missing pricing", target_query="Bright Data pricing 2026", priority=2)
    g2 = EvidenceGap.model_validate_json(g.model_dump_json())
    assert g2.description == "Missing pricing"
    assert g2.target_query == "Bright Data pricing 2026"
    assert g2.priority == 2


@pytest.mark.unit
def test_evidence_store_roundtrip():
    s = EvidenceStore(
        claims=[Claim(id="a", text="x")],
        gaps=[EvidenceGap(description="gap")],
    )
    s2 = EvidenceStore.model_validate_json(s.model_dump_json())
    assert s2.claims[0].id == "a"
    assert s2.gaps[0].description == "gap"


@pytest.mark.unit
def test_dedupe_claims_keeps_first_by_id():
    """dedupe_claims merges lists and drops later duplicates by id."""
    c1 = Claim(id="c1", text="first")
    c1_dup = Claim(id="c1", text="second")
    c2 = Claim(id="c2", text="third")
    result = dedupe_claims([c1], [c1_dup, c2])
    assert [x.id for x in result] == ["c1", "c2"]
    # First occurrence wins: text must stay "first", not be overwritten by "second"
    assert result[0].text == "first"


@pytest.mark.unit
def test_dedupe_claims_handles_none():
    assert dedupe_claims(None, None) == []
    assert [x.id for x in dedupe_claims(None, [Claim(id="n", text="1")])] == ["n"]
    assert dedupe_claims([Claim(id="m", text="2")], None) is not None