"""
Unit tests for LangGraph orchestration: state, nodes, and graph compilation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from searchops.core.interfaces.scraper import ScrapeMode, ScrapeResult
from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.orchestration.graphs.deep_research import build_deep_research_graph
from searchops.orchestration.nodes.extract_knowledge import extract_knowledge_node
from searchops.orchestration.nodes.planner import planner_node
from searchops.orchestration.nodes.report_writer import report_writer_node
from searchops.orchestration.nodes.scrape import scrape_node
from searchops.orchestration.nodes.search import search_node
from searchops.orchestration.states.research_state import ResearchState
from searchops.scraping.pipeline import ScrapingPipeline
from searchops.search.aggregator import FederatedSearchAggregator
from searchops.search.contracts import SearchResultItem


# ── Graph compilation ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_deep_research_graph_compiles():
    """Graph must compile without raising even with mock dependencies."""
    mock_llm = AsyncMock()
    mock_agg = AsyncMock(spec=FederatedSearchAggregator)
    mock_pipe = AsyncMock(spec=ScrapingPipeline)
    mock_ext = AsyncMock()

    compiled = build_deep_research_graph(
        llm_router=mock_llm,
        aggregator=mock_agg,
        scraping_pipeline=mock_pipe,
        extractor=mock_ext,
    )
    assert compiled is not None


# ── Individual node tests ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_planner_node():
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = (
        "1. What are the core concepts of quantum computing?\n"
        "2. Practical applications of quantum algorithms\n"
        "3. Quantum hardware landscape 2024"
    )
    state: ResearchState = {"query": "quantum computing", "max_sources": 5}
    result = await planner_node(state, llm_router=mock_llm)

    assert result["iteration"] == 0
    assert result["plan"].primary_query == "quantum computing"
    assert len(result["plan"].sub_queries) == 3
    assert result["plan"].sub_queries[0] == "What are the core concepts of quantum computing?"
    assert result["budget"].remaining_searches == 4
    mock_llm.generate.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_node():
    mock_agg = AsyncMock(spec=FederatedSearchAggregator)
    mock_agg.aggregate_search.return_value = [
        SearchResultItem(
            title="Quantum Intro",
            url="https://example.com/q1",
            snippet="Quantum basics",
            provider="tavily",
            score=0.95,
        )
    ]

    state: ResearchState = {"query": "quantum computing", "max_sources": 5}
    result = await search_node(state, aggregator=mock_agg)

    assert len(result["search_results"]) == 1
    assert result["urls_to_scrape"] == ["https://example.com/q1"]
    assert len(result["search_executions"]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ranker_node():
    from searchops.orchestration.nodes.ranker import ranker_node

    items = [
        SearchResultItem(title="Q1", url="https://example.com/q1?utm_source=test", snippet="s1", provider="p1", score=0.8),
        SearchResultItem(title="Q1 Dup", url="https://example.com/q1/", snippet="s1 dup", provider="p2", score=0.9),
        SearchResultItem(title="Q2", url="https://example.com/q2", snippet="s2", provider="p1", score=0.7),
    ]
    state: ResearchState = {"search_results": items, "max_sources": 5}
    result = await ranker_node(state)

    assert len(result["urls_to_scrape"]) == 2
    assert "https://example.com/q1" in result["urls_to_scrape"][0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_pruner_node():
    from searchops.orchestration.nodes.state_pruner import state_pruner_node

    state: ResearchState = {
        "scraped_contents": [
            {"url": "https://example.com", "title": "T1", "content": "VERY LONG RAW HTML TEXT BLOBS"}
        ]
    }
    result = await state_pruner_node(state)

    assert result["scraped_contents"][0]["content"] == ""
    assert result["scraped_contents"][0]["url"] == "https://example.com"




@pytest.mark.unit
@pytest.mark.asyncio
async def test_scrape_node():
    mock_pipeline = AsyncMock(spec=ScrapingPipeline)
    mock_pipeline.execute.return_value = ScrapeResult(
        url="https://example.com/q1",
        final_url="https://example.com/q1",
        status_code=200,
        markdown="# Quantum Computing\nIt uses qubits.",
        title="Quantum Intro",
        scrape_mode_used=ScrapeMode.FIRECRAWL,
    )

    state: ResearchState = {"urls_to_scrape": ["https://example.com/q1"]}
    result = await scrape_node(state, pipeline=mock_pipeline)

    assert len(result["scraped_contents"]) == 1
    assert result["scraped_contents"][0]["title"] == "Quantum Intro"
    assert result["failed_urls"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_knowledge_node():
    mock_extractor = AsyncMock()
    e1 = KGEntity(name="Qubit", entity_type="Concept", description="Quantum bit")
    r1 = KGRelation(
        source_id=e1.id,
        target_id=e1.id,
        relation_type="PART_OF",
        description="Building block",
    )
    mock_extractor.extract.return_value = ([e1], [r1])

    state: ResearchState = {
        "scraped_contents": [{"url": "u1", "content": "Qubits are key.", "title": ""}]
    }
    result = await extract_knowledge_node(state, extractor=mock_extractor)

    assert len(result["entities"]) == 1
    assert len(result["relations"]) == 1
    assert result["entities"][0].name == "Qubit"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_report_writer_node():
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = "# Research Report\n\nQuantum computing uses qubits."

    state: ResearchState = {
        "query": "quantum computing",
        "scraped_contents": [
            {"url": "https://example.com", "title": "Q Intro", "content": "Qubits..."}
        ],
        "entities": [],
    }
    result = await report_writer_node(state, llm_router=mock_llm)

    assert "# Research Report" in result["final_report"]
    assert "https://example.com" in result["citations"]
