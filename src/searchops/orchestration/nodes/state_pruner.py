"""
State Pruner & Memory Compaction node: removes raw scraped text blobs from state after extraction.
"""

from __future__ import annotations

import structlog

from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


async def state_pruner_node(state: ResearchState) -> ResearchState:
    """State compaction node: prunes raw scraped_contents from state memory before report synthesis."""
    scraped = state.get("scraped_contents", [])
    entities = state.get("entities", [])
    log.info("State pruner executing memory compaction", raw_docs=len(scraped), extracted_entities=len(entities))

    # Keep only URL & Title in scraped_contents for citation tracking, prune raw document text
    compacted_scraped = [
        {"url": doc.get("url", ""), "title": doc.get("title", ""), "content": ""}
        for doc in scraped
    ]

    return {"scraped_contents": compacted_scraped}  # type: ignore[misc]
