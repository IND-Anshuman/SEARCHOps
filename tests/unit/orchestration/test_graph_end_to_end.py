"""
End-to-end run of the real deep-research graph with stubbed I/O.

The LLM, search and scraper are replaced by deterministic stubs; every node,
edge and state reducer is the production code. Unit tests on single nodes
passed while the assembled graph threw away verifier verdicts, re-searched
the same queries every loop and dropped the report settings — these tests
check the graph as a whole so those regressions cannot come back.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

from searchops.orchestration.graphs.deep_research import build_deep_research_graph
from searchops.search.contracts import SearchResultItem

PAGES = {
    "alpha": (
        "Navigation | Home | About\n\n"
        "Qdrant is an open-source vector database written in Rust. It was first released in 2021 "
        "and supports filtering on payload fields during search.\n\n"
        "The project reports query latency under 10 milliseconds on a million vectors with HNSW indexing."
    ),
    "beta": (
        "Milvus is a vector database created by Zilliz. It was open-sourced in 2019 and is a graduated "
        "project of the LF AI and Data Foundation.\n\n"
        "Independent benchmarks show Qdrant is written in Rust and achieves low query latency on large collections."
    ),
    "gamma": (
        "Pinecone is a managed vector database service founded in 2019. It does not offer a self-hosted "
        "open-source edition, unlike Qdrant and Milvus which both publish their source code."
    ),
}


class StubLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    def _log(self, kind: str, prompt: str) -> None:
        self.calls.append(kind)
        self.prompts.setdefault(kind, []).append(prompt)

    async def generate(self, prompt: str, system_prompt: str | None = None, **_: Any) -> str:
        sp = system_prompt or ""
        if "research planning" in prompt:
            self._log("planner", prompt)
            return "1. qdrant vector database\n2. milvus vector database"
        if "evidence extractor" in sp:
            self._log("extract", prompt)
            claims = []
            for label, body in re.findall(r"\[(P\d+)\]\n(.+?)(?=\n\n\[P\d+\]|\Z)", prompt, flags=re.S):
                first = re.split(r"(?<=[.])\s", body.strip())[0]
                claims.append({"text": first, "passage": label, "quote": first, "confidence": 0.8})
            # One claim with a fabricated quote that must be dropped.
            claims.append(
                {
                    "text": "Qdrant was acquired by Google.",
                    "passage": "P1",
                    "quote": "Qdrant was acquired by Google in 2023.",
                    "confidence": 0.9,
                }
            )
            return json.dumps(
                {"claims": claims, "entities": [{"name": "Qdrant", "type": "Technology"}], "relations": []}
            )
        if "research critic" in sp:
            self._log("critic", prompt)
            if self.calls.count("critic") == 1:
                return json.dumps(
                    {
                        "gaps": [
                            {
                                "description": "Managed alternatives are not covered",
                                "target_query": "pinecone managed vector database",
                                "priority": 1,
                            }
                        ]
                    }
                )
            return json.dumps({"gaps": []})
        if "fact-checker" in sp:
            self._log("verifier", prompt)
            results = []
            for block in prompt.split("CLAIM [id=")[1:]:
                cid = block.split("]", 1)[0]
                labels = dict.fromkeys(re.findall(r"\[(E\d+)\]", block), "supports")
                results.append({"claim_id": cid, "labels": labels, "reason": "stated"})
            return json.dumps({"results": results})
        if "report architect" in sp.lower() or "design the structure" in prompt.lower() or "sections" in sp.lower():
            self._log("report_plan", prompt)
            return json.dumps(
                {
                    "sections": [
                        {
                            "name": "Overview",
                            "purpose": "What these databases are",
                            "target_words": 300,
                            "subsections": [],
                            "evidence_topics": ["vector database"],
                        },
                        {
                            "name": "Comparison",
                            "purpose": "How they differ",
                            "target_words": 300,
                            "subsections": [],
                            "evidence_topics": ["open-source"],
                        },
                    ],
                    "total_words": 600,
                }
            )
        self._log("section", prompt)
        refs = re.findall(r"\[(\d+)\]", prompt.split("### Sources")[1]) if "### Sources" in prompt else []
        ref = refs[0] if refs else "1"
        return f"Qdrant is written in Rust [{ref}]. A made-up citation [99]."


class StubSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, q: Any) -> list[SearchResultItem]:
        self.queries.append(q.query)
        key = "gamma" if "pinecone" in q.query else ("beta" if "milvus" in q.query else "alpha")
        return [SearchResultItem(url=f"https://example.com/{key}", title=key.title(), snippet="s", provider="stub")]


class StubScraper:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    async def execute(self, req: Any) -> Any:
        self.fetched.append(req.url)
        key = req.url.rsplit("/", 1)[-1]
        return SimpleNamespace(status_code=200, markdown=PAGES[key], html="", title=key.title())


async def _run(**overrides: Any) -> tuple[dict[str, Any], StubLLM, StubSearch, StubScraper]:
    llm, search, scraper = StubLLM(), StubSearch(), StubScraper()
    graph = build_deep_research_graph(llm_router=llm, orchestrator=search, scraping_pipeline=scraper)
    state: dict[str, Any] = {
        "query": "compare open source vector databases",
        "depth": "standard",
        "max_sources": 5,
        "scrape_plan": {"mode": "precision", "max_pages": 5, "max_depth": 0},
        "target_pages": 2,
        "report_structure_prompt": "Focus on licensing.",
    }
    state.update(overrides)
    final = await graph.ainvoke(state, {"recursion_limit": 80})
    return final, llm, search, scraper


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verifier_verdicts_survive_into_final_state() -> None:
    final, *_ = await _run()
    statuses = {c.status for c in final["evidence_claims"]}
    assert statuses == {"supported"}, statuses
    assert all(c.supporting_urls for c in final["evidence_claims"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gap_follow_up_is_searched_and_nothing_is_repeated() -> None:
    _, llm, search, scraper = await _run()
    assert "pinecone managed vector database" in search.queries
    assert len(search.queries) == len({q.lower() for q in search.queries}), search.queries
    assert len(scraper.fetched) == len(set(scraper.fetched)), scraper.fetched
    assert llm.calls.count("planner") == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claims_are_grounded_in_page_text() -> None:
    final, *_ = await _run()
    texts = [c.text for c in final["evidence_claims"]]
    assert not any("Google" in t for t in texts), "a claim with a fabricated quote was kept"
    for claim in final["evidence_claims"]:
        cit = claim.citations[0]
        assert cit.quote_verified and cit.passage_id
        assert cit.url.startswith("https://example.com/")
    # Navigation chrome is not evidence.
    assert not any("Navigation" in p.text for p in final["passages"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sources_and_corpus_accumulate_across_loops() -> None:
    final, *_ = await _run()
    urls = {d["url"] for d in final["scraped_contents"]}
    assert urls == {"https://example.com/alpha", "https://example.com/beta", "https://example.com/gamma"}
    assert "Pinecone" in final["evidence_corpus"] and "Milvus" in final["evidence_corpus"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_report_citations_resolve_and_settings_reach_the_writer() -> None:
    final, llm, *_ = await _run()
    report = final["final_report"]
    numbers = {int(n) for n in re.findall(r"\[(\d+)\](?!\()", report)}
    registry = {s["n"] for s in final["report_sources"]}
    assert numbers and numbers <= registry, (numbers, registry)
    assert "[99]" not in report
    assert "## Sources" in report
    assert any("Focus on licensing." in p for p in llm.prompts.get("report_plan", []) + llm.prompts.get("section", []))
    stats = final["evaluation_stats"]
    assert stats["grounding_rate"] == 1.0
    assert stats["status_mix"] == {"supported": len(final["evidence_claims"])}
