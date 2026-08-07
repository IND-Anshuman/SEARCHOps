"""
Context Window Delta Compressor Service.

Isolates state deltas (+delta_entities, +delta_relations, +delta_search_results)
between state iterations (N-1 vs N) to compress prompt sizes for multi-hop agent execution.
"""

from __future__ import annotations

from typing import Any, Protocol
import structlog
from pydantic import BaseModel, Field

from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.llm.tokenizer import count_tokens
from searchops.orchestration.states.research_state import ResearchState
from searchops.search.contracts import SearchResultItem

log = structlog.get_logger(__name__)


class CompressedContextDelta(BaseModel):
    """Container holding computed context window delta for multi-hop prompt compilation."""

    delta_entities: list[dict[str, Any]] = Field(default_factory=list)
    delta_relations: list[dict[str, Any]] = Field(default_factory=list)
    delta_search_results: list[dict[str, Any]] = Field(default_factory=list)
    static_system_prompt: str = ""
    formatted_delta_prompt: str = ""
    estimated_tokens: int = 0
    compression_ratio: float = 0.0


class IContextCompressor(Protocol):
    """Protocol for Context Window Delta Compression."""

    async def compress_context_delta(
        self,
        base_state: ResearchState | None,
        current_state: ResearchState,
        max_delta_tokens: int = 1500,
    ) -> CompressedContextDelta:
        ...


class ContextDeltaCompressor:
    """Computes context window deltas across multi-hop agent research iterations."""

    def __init__(self, model_name: str = "gpt-4o") -> None:
        self.model_name = model_name

    async def compress_context_delta(
        self,
        base_state: ResearchState | None,
        current_state: ResearchState,
        max_delta_tokens: int = 1500,
    ) -> CompressedContextDelta:
        """Compute state delta between base_state (iteration N-1) and current_state (iteration N)."""
        curr_entities = current_state.get("entities", []) or []
        curr_relations = current_state.get("relations", []) or []
        curr_search = current_state.get("search_results", []) or []

        base_entities = base_state.get("entities", []) if base_state else []
        base_relations = base_state.get("relations", []) if base_state else []
        base_search = base_state.get("search_results", []) if base_state else []

        base_entity_ids = {e.canonical_id for e in base_entities if hasattr(e, "canonical_id")}
        base_rel_keys = {
            f"{r.source_canonical_id or r.source_id}:{r.relation_type}:{r.target_canonical_id or r.target_id}"
            for r in base_relations
            if hasattr(r, "relation_type")
        }
        base_search_urls = {s.url for s in base_search if hasattr(s, "url") and s.url}

        delta_entities = [
            {"name": e.name, "type": e.entity_type, "description": e.description}
            for e in curr_entities
            if hasattr(e, "canonical_id") and e.canonical_id not in base_entity_ids
        ]

        delta_relations = [
            {
                "source": r.source_canonical_id or r.source_id,
                "type": r.relation_type,
                "target": r.target_canonical_id or r.target_id,
            }
            for r in curr_relations
            if hasattr(r, "relation_type")
            and f"{r.source_canonical_id or r.source_id}:{r.relation_type}:{r.target_canonical_id or r.target_id}" not in base_rel_keys
        ]

        delta_search = [
            {"title": s.title, "url": s.url, "snippet": getattr(s, "snippet", "")}
            for s in curr_search
            if hasattr(s, "url") and s.url and s.url not in base_search_urls
        ]

        # Calculate Full Prompt vs Delta Prompt Token Comparison
        full_text = f"Entities: {curr_entities}\nRelations: {curr_relations}\nSearch: {curr_search}"
        delta_text = f"Delta Entities: {delta_entities}\nDelta Relations: {delta_relations}\nDelta Search: {delta_search}"

        full_tokens = max(1, count_tokens(full_text, self.model_name))
        delta_tokens = count_tokens(delta_text, self.model_name)

        compression_ratio = max(0.0, round(1.0 - (delta_tokens / full_tokens), 4))

        log.info(
            "Computed context window delta",
            delta_entities=len(delta_entities),
            delta_relations=len(delta_relations),
            delta_search=len(delta_search),
            full_tokens=full_tokens,
            delta_tokens=delta_tokens,
            compression_ratio=compression_ratio,
        )

        return CompressedContextDelta(
            delta_entities=delta_entities,
            delta_relations=delta_relations,
            delta_search_results=delta_search,
            static_system_prompt="You are an enterprise research analyst operating under sliding-window context compression.",
            formatted_delta_prompt=delta_text,
            estimated_tokens=delta_tokens,
            compression_ratio=compression_ratio,
        )
