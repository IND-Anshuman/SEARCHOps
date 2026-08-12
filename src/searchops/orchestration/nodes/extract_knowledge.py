"""
Knowledge Extraction node: runs the LLM entity extractor over scraped content.

Free-tier note: each document is capped at 1 200 chars before extraction
to stay well inside all provider context windows. State is automatically
compacted post-extraction to prune raw document strings.
"""

from __future__ import annotations

import structlog
import asyncio

from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.token_budget import MAX_DOC_CHARS
from searchops.orchestration.nodes.state_compressor import StateTokenOptimizer
from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


async def extract_knowledge_node(
    state: ResearchState,
    *,
    extractor: EntityExtractor,
) -> ResearchState:
    """Concurrently run entity & relation extraction over all scraped_contents."""
    scraped = state.get("scraped_contents", [])
    log.info("Knowledge extraction node executing", doc_count=len(scraped))

    valid_contents = [
        doc.get("content", doc.get("content_summary", "")).strip()[:MAX_DOC_CHARS]
        for doc in scraped
        if doc.get("content", doc.get("content_summary", "")).strip()
    ]

    if not valid_contents:
        return {"entities": [], "relations": []}  # type: ignore[misc]

    # Perform concurrent LLM entity extraction across documents
    tasks = [extractor.extract(content) for content in valid_contents]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_entities: list[KGEntity] = []
    all_relations: list[KGRelation] = []

    for res in results:
        if isinstance(res, Exception):
            log.error("Document entity extraction failed", error=str(res))
            continue
        entities, relations = res
        all_entities.extend(entities)
        all_relations.extend(relations)

    # Perform post-extraction state compaction to strip raw text blobs
    optimizer = StateTokenOptimizer()
    compacted_scraped = optimizer.compact_scraped_contents(scraped)

    log.info(
        "Concurrent extraction & state compaction complete",
        entities=len(all_entities),
        relations=len(all_relations),
        compacted_docs=len(compacted_scraped),
    )
    return {
        "entities": all_entities,
        "relations": all_relations,
        "scraped_contents": compacted_scraped,
    }  # type: ignore[misc]
