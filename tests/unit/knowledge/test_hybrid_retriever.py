"""
Unit tests for HybridRetriever.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from searchops.knowledge.hybrid_retriever import HybridRetriever


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hybrid_retriever_concurrent_execution():
    mock_vector = AsyncMock()
    mock_graph = AsyncMock()

    mock_vector.search_similar.return_value = [
        {"score": 0.92, "payload": {"content": "Sample Chunk 1"}},
    ]
    mock_graph.extract_subgraph.return_value = {
        "nodes": [{"canonical_id": "tech:qc", "name": "Quantum Computing"}],
        "edges": [],
    }

    retriever = HybridRetriever(vector_repo=mock_vector, graph_repo=mock_graph)
    res = await retriever.retrieve_context(
        collection_name="research_chunks",
        query_vector=[0.1] * 1536,
        canonical_ids=["tech:qc"],
    )

    assert len(res["vector_chunks"]) == 1
    assert res["vector_chunks"][0]["score"] == 0.92
    assert len(res["graph_subgraph"]["nodes"]) == 1
    assert res["graph_subgraph"]["nodes"][0]["name"] == "Quantum Computing"

    mock_vector.search_similar.assert_called_once()
    mock_graph.extract_subgraph.assert_called_once_with(["tech:qc"], max_hops=2)
