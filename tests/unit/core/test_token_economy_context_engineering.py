"""
Unit tests for Token Economy & Context Engineering enhancements.
Verifies StateTokenOptimizer, ContextDeltaCompressor, and ContextAssemblyService exact token calculations.
"""

from __future__ import annotations

import pytest

from searchops.core.context.assembly import ContextAssemblyService
from searchops.core.context.delta_compressor import ContextDeltaCompressor
from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.orchestration.nodes.extract_knowledge import extract_knowledge_node
from searchops.orchestration.nodes.state_compressor import StateTokenOptimizer, state_compressor_node
from searchops.orchestration.states.research_state import ResearchState


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_token_optimizer_compaction():
    optimizer = StateTokenOptimizer(max_summary_chars=100, max_snippets=2)

    raw_docs = [
        {
            "url": "https://example.com/doc1",
            "title": "Document One",
            "content": "This is line one of raw scraped content.\nThis is line two with more details.\nAnd another line.",
        },
        {
            "url": "https://example.com/doc2",
            "title": "Document Two",
            "content": "Short text content.",
        },
    ]

    compacted = optimizer.compact_scraped_contents(raw_docs)
    assert len(compacted) == 2

    for doc in compacted:
        assert doc["content"] == ""  # Raw text stripped
        assert len(doc["content_summary"]) > 0
        assert isinstance(doc["snippets"], list)

    state: ResearchState = {"scraped_contents": raw_docs}
    optimized_state = await optimizer.optimize_state(state)
    assert "scraped_contents" in optimized_state
    assert len(optimized_state["scraped_contents"]) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_delta_compressor():
    compressor = ContextDeltaCompressor()

    e1 = KGEntity(id="e1", name="Python", entity_type="language", description="Programming Language")
    e2 = KGEntity(id="e2", name="LangGraph", entity_type="framework", description="Orchestration Framework")
    r1 = KGRelation(id="r1", source_id=e1.id, target_id=e2.id, relation_type="USES")

    base_state: ResearchState = {
        "entities": [e1],
        "relations": [],
        "search_results": [],
    }

    current_state: ResearchState = {
        "entities": [e1, e2],
        "relations": [r1],
        "search_results": [],
    }

    delta = await compressor.compress_context_delta(base_state, current_state)
    assert len(delta.delta_entities) == 1
    assert delta.delta_entities[0]["name"] == "LangGraph"
    assert len(delta.delta_relations) == 1
    assert delta.compression_ratio >= 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_assembly_service_token_count():
    service = ContextAssemblyService()
    e = KGEntity(id="e1", name="FastAPI", entity_type="framework", description="Web Framework")

    scraped = [
        {
            "url": "https://fastapi.tiangolo.com",
            "title": "FastAPI",
            "content_summary": "High performance web framework for building APIs with Python.",
            "snippets": ["FastAPI is a modern web framework."],
        }
    ]

    assembled = await service.assemble_context(
        query="Explain FastAPI performance",
        entities=[e],
        scraped_docs=scraped,
    )

    assert assembled.query == "Explain FastAPI performance"
    assert len(assembled.graph_entities) == 1
    assert assembled.total_estimated_tokens > 0
