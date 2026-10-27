"""Tests for the evaluation node and the evidence-quality metrics.

These metrics were library code with no production caller. The evaluation
node now runs in the pipeline and publishes them with the job, so they are
measured from the run's own artefacts rather than asserted in isolation.
"""

from __future__ import annotations

import pytest

from searchops.knowledge.evaluation.metrics import citation_coverage, hallucination_rate
from searchops.orchestration.nodes.evaluation import evaluation_node
from searchops.orchestration.nodes.state_pruner import state_pruner_node


class _Citation:
    def __init__(self, url: str) -> None:
        self.url = url


class _Claim:
    def __init__(self, cid: str, text: str, status: str = "supported", urls: tuple[str, ...] = ()) -> None:
        self.id = cid
        self.text = text
        self.status = status
        self.citations = [_Citation(u) for u in urls]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_evaluation_reports_coverage_and_status_mix():
    state = {
        "evidence_claims": [
            _Claim("c1", "Surface codes scale logarithmically.", "supported", ("https://ibm.com/a",)),
            _Claim("c2", "Fault tolerance needs many qubits.", "contested", ()),
            _Claim("c3", "Decoherence limits coherence times.", "unverified", ("https://nature.com/b",)),
        ],
        "evidence_corpus": "Surface codes scale logarithmically with code distance.",
    }
    delta = await evaluation_node(state)
    stats = delta["evaluation_stats"]

    assert stats["total_claims"] == 3
    assert stats["cited_claims"] == 2
    assert stats["citation_coverage"] == round(2 / 3, 4)
    assert stats["status_mix"] == {"supported": 1, "contested": 1, "unverified": 1}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_evaluation_says_so_when_there_are_no_claims():
    delta = await evaluation_node({"evidence_claims": [], "evidence_corpus": "text"})
    stats = delta["evaluation_stats"]
    assert stats["total_claims"] == 0
    assert stats["hallucination_measured"] is False
    assert "no evidence metrics" in stats["note"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_evaluation_does_not_measure_hallucination_without_a_corpus():
    """Without a corpus there is no ground truth, so no number is published."""
    delta = await evaluation_node(
        {
            "evidence_claims": [_Claim("c1", "Nvidia makes GPUs.", "supported", ("https://n.com",))],
            "evidence_corpus": "",
        }
    )
    stats = delta["evaluation_stats"]
    assert stats["hallucination_rate"] is None
    assert stats["hallucination_measured"] is False
    assert "not measured" in stats["note"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_pruner_keeps_the_corpus_it_strips():
    """The pruner drops the raw page text; the corpus must survive for the
    evaluation node that runs after it."""
    scraped = [
        {"url": "https://a.com", "title": "A", "content": "Alpha beta gamma."},
        {"url": "https://b.com", "title": "B", "content": "Delta epsilon."},
    ]
    delta = await state_pruner_node({"scraped_contents": scraped, "entities": []})

    # The raw text is gone...
    assert all(page["content"] == "" for page in delta["scraped_contents"])
    # ...but the corpus was captured while it still existed.
    assert "Alpha beta gamma." in delta["evidence_corpus"]
    assert "Delta epsilon." in delta["evidence_corpus"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_pruner_does_not_replace_a_real_corpus_with_an_empty_one():
    """The extraction node captures the corpus first; the pruner must not
    overwrite it with the empty text that is all that is left by then."""
    delta = await state_pruner_node(
        {
            "scraped_contents": [{"url": "https://a.com", "title": "A", "content": ""}],
            "entities": [],
            "evidence_corpus": "The real retrieved text.",
        }
    )
    assert delta["evidence_corpus"] == "The real retrieved text."


@pytest.mark.unit
def test_build_corpus_is_bounded():
    """The corpus must not re-inflate the state the compaction removed."""
    from searchops.orchestration.nodes.state_compressor import build_corpus

    pages = [{"url": f"https://{i}.com", "title": str(i), "content": "x" * 1000} for i in range(500)]
    corpus = build_corpus(pages)
    assert len(corpus) <= 200_000
    assert len(corpus) > 0


@pytest.mark.unit
def test_citation_coverage_reads_dict_claims():
    """Regression: the metric used getattr only, so a dict claim read as
    uncited and coverage came back 0% for a fully cited report."""
    claims = [
        {"id": "c1", "text": "A", "citations": [{"url": "https://a.com"}]},
        {"id": "c2", "text": "B", "citations": []},
    ]
    score = citation_coverage(claims)
    assert score.total == 2
    assert score.cited == 1
    assert score.coverage == 0.5


@pytest.mark.unit
def test_hallucination_rate_reads_dict_claims():
    claims = [
        {"id": "c1", "text": "Surface codes scale logarithmically.", "status": "supported"},
        {"id": "c2", "text": "Zorblax drives the reactor.", "status": "supported"},
    ]
    score = hallucination_rate(claims, "Surface codes scale logarithmically with code distance.")
    assert score.checked == 2
    assert score.hallucinated == 1
    assert "Zorblax" in score.offending
