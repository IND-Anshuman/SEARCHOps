"""
Context Assembly Service: Assembles GraphRAG entity subgraphs, vector chunks, history, and citations.
"""

from __future__ import annotations

from typing import Any
import structlog
from pydantic import BaseModel, Field

from searchops.core.context.delta_compressor import ContextDeltaCompressor, CompressedContextDelta
from searchops.core.interfaces.storage import IGraphStore, IVectorStore
from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.llm.tokenizer import count_tokens

log = structlog.get_logger(__name__)


class AssembledContext(BaseModel):
    """Container holding assembled domain context for prompt compilation."""

    query: str
    graph_entities: list[dict[str, Any]] = Field(default_factory=list)
    graph_relations: list[dict[str, Any]] = Field(default_factory=list)
    vector_excerpts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    total_estimated_tokens: int = 0
    delta: CompressedContextDelta | None = None


class ContextAssemblyService:
    """Service responsible for gathering and synthesizing multi-modal context for agents."""

    def __init__(
        self,
        vector_store: IVectorStore | None = None,
        graph_store: IGraphStore | None = None,
        delta_compressor: ContextDeltaCompressor | None = None,
    ) -> None:
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.delta_compressor = delta_compressor or ContextDeltaCompressor()

    async def assemble_context(
        self,
        query: str,
        entities: list[KGEntity] | None = None,
        scraped_docs: list[dict[str, Any]] | None = None,
    ) -> AssembledContext:
        """Assemble domain context across Graph, Vector, and Scraped excerpts."""
        graph_entities = [
            {"name": e.name, "type": e.entity_type, "description": e.description}
            for e in (entities or [])
        ]
        vector_excerpts = [
            {
                "url": doc.get("url", ""),
                "title": doc.get("title", ""),
                "content": doc.get("content_summary") or doc.get("content", ""),
                "snippets": doc.get("snippets", []),
            }
            for doc in (scraped_docs or [])
        ]
        citations = [doc.get("url", "") for doc in (scraped_docs or []) if doc.get("url")]

        context_string = f"Query: {query}\nEntities: {graph_entities}\nDocs: {vector_excerpts}"
        total_tokens = count_tokens(context_string, "gpt-4o")

        log.info("Assembled domain context", query=query, entities=len(graph_entities), docs=len(vector_excerpts), total_tokens=total_tokens)

        return AssembledContext(
            query=query,
            graph_entities=graph_entities,
            vector_excerpts=vector_excerpts,
            citations=citations,
            total_estimated_tokens=total_tokens,
        )
