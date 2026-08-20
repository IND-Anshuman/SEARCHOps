"""
Unit tests for Knowledge Extractor, Hybrid Retriever, and Community Detection.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from searchops.knowledge.extractor import EntityExtractor
from searchops.knowledge.hybrid_retriever import HybridRetriever
from searchops.knowledge.community import HierarchicalCommunityDetector
from searchops.knowledge.domain.entity import KGEntity, KGRelation


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entity_extractor_full():
    mock_router = AsyncMock()
    mock_router.generate.return_value = '{"entities": [{"name": "Python", "type": "Technology", "description": "Language"}], "relations": []}'

    extractor = EntityExtractor(llm_router=mock_router)
    entities, relations = await extractor.extract("Python is a programming language.")

    assert len(entities) == 1
    assert entities[0].name == "Python"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hybrid_retriever_full():
    mock_vector = AsyncMock()
    mock_graph = AsyncMock()
    mock_vector.search_similar.return_value = []
    mock_graph.extract_subgraph.return_value = {"nodes": [], "edges": []}

    retriever = HybridRetriever(vector_repo=mock_vector, graph_repo=mock_graph)
    results = await retriever.retrieve_context("test_col", [0.1, 0.2], ["c1"])
    assert results is not None
    assert "vector_chunks" in results


@pytest.mark.unit
def test_community_detector_full():
    detector = HierarchicalCommunityDetector()
    e1 = KGEntity(canonical_id="n1", name="Node 1", entity_type="tech")
    e2 = KGEntity(canonical_id="n2", name="Node 2", entity_type="tech")
    rel = KGRelation(source_id="n1", target_id="n2", source_canonical_id="n1", target_canonical_id="n2", relation_type="uses")

    G = detector.build_network_graph([e1, e2], [rel])
    communities = detector.detect_communities(G)
    assert isinstance(communities, list)
