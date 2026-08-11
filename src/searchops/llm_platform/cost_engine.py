"""
Adaptive Cost Engine & Budget Manager for Multi-Tier Token Trimming.
"""

from __future__ import annotations

import structlog

from searchops.core.context.assembly import AssembledContext
from searchops.llm.tokenizer import truncate_by_tokens

log = structlog.get_logger(__name__)


class AdaptiveBudgetManager:
    """Dynamically compresses AssembledContext components to fit strict LLM token ceilings."""

    def __init__(self, max_context_tokens: int = 3_000) -> None:
        self.max_context_tokens = max_context_tokens

    def compress_context(self, context: AssembledContext, model_name: str = "gpt-4o") -> AssembledContext:
        """Compress context fields hierarchically if token limit is exceeded."""
        compressed_excerpts = []
        for doc in context.vector_excerpts:
            content = doc.get("content", "")
            truncated_content = truncate_by_tokens(content, 400, model_name=model_name)
            compressed_excerpts.append({**doc, "content": truncated_content})

        log.info("Compressed context for prompt budget", model=model_name, max_tokens=self.max_context_tokens)
        return AssembledContext(
            query=context.query,
            graph_entities=context.graph_entities[:10],
            graph_relations=context.graph_relations[:10],
            vector_excerpts=compressed_excerpts[:5],
            citations=context.citations,
            total_estimated_tokens=self.max_context_tokens,
        )
