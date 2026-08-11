"""
Hybrid Retriever Engine.

Combines Qdrant dense vector search with Neo4j GraphRAG k-hop
subgraph extraction concurrently via asyncio.gather.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from searchops.infrastructure.vector.qdrant import QdrantVectorRepository
from searchops.knowledge.repository import Neo4jGraphRepository

log = structlog.get_logger(__name__)


class HybridRetriever:
    """Enterprise dual-store Hybrid Vector and GraphRAG context retriever."""

    def __init__(
        self,
        vector_repo: QdrantVectorRepository | None = None,
        graph_repo: Neo4jGraphRepository | None = None,
    ) -> None:
        self.vector_repo = vector_repo
        self.graph_repo = graph_repo

    async def retrieve_context(
        self,
        collection_name: str,
        query_vector: list[float],
        canonical_ids: list[str],
        top_k_vector: int = 5,
        max_hops: int = 2,
    ) -> dict[str, Any]:
        """Perform concurrent dense vector search and GraphRAG subgraph traversal."""
        log.info("Starting hybrid retrieval", collection=collection_name, canonical_ids=canonical_ids)

        vector_task = (
            self.vector_repo.search_similar(collection_name, query_vector, limit=top_k_vector)
            if self.vector_repo
            else asyncio.sleep(0, result=[])
        )
        graph_task = (
            self.graph_repo.extract_subgraph(canonical_ids, max_hops=max_hops)
            if self.graph_repo
            else asyncio.sleep(0, result={"nodes": [], "edges": []})
        )

        results = await asyncio.gather(vector_task, graph_task, return_exceptions=True)

        vector_res = results[0] if not isinstance(results[0], Exception) else []
        graph_res = results[1] if not isinstance(results[1], Exception) else {"nodes": [], "edges": []}

        if isinstance(results[0], Exception):
            log.error("Vector retrieval failed during hybrid query", error=str(results[0]))
        if isinstance(results[1], Exception):
            log.error("GraphRAG retrieval failed during hybrid query", error=str(results[1]))

        return {
            "vector_chunks": vector_res,
            "graph_subgraph": graph_res,
        }
