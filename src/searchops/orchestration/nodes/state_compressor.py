"""
State Compaction & Memory Optimization Node.

Prunes raw scraped HTML/markdown text blobs from ResearchState immediately after
knowledge extraction, replacing them with token-dense document summaries and structural snippets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


class IStateOptimizer(Protocol):
    """Protocol for State Memory Compaction."""

    async def optimize_state(self, state: ResearchState) -> ResearchState: ...


class StateTokenOptimizer:
    """Optimizes ResearchState memory footprint by stripping raw document content."""

    def __init__(self, max_summary_chars: int = 300, max_snippets: int = 3) -> None:
        self.max_summary_chars = max_summary_chars
        self.max_snippets = max_snippets

    def compact_scraped_contents(self, scraped_contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compact raw scraped contents by stripping raw content and building summaries/snippets."""
        compacted: list[dict[str, Any]] = []

        for doc in scraped_contents:
            url = doc.get("url", "")
            title = doc.get("title", "")
            raw_content = doc.get("content", "") or ""

            # Preserve pre-existing summary/snippets if present
            summary = doc.get("content_summary", "")
            snippets = doc.get("snippets", [])

            if not summary and raw_content:
                # Build concise summary from leading content text
                summary = raw_content.strip()[: self.max_summary_chars].replace("\n", " ") + "..."

            if not snippets and raw_content:
                # Extract structural non-empty line snippets
                lines = [line.strip() for line in raw_content.splitlines() if len(line.strip()) > 15]
                snippets = lines[: self.max_snippets]

            compacted.append(
                {
                    "url": url,
                    "title": title,
                    "content": "",  # Prune raw text blob
                    "content_summary": summary,
                    "snippets": snippets,
                }
            )

        return compacted

    async def optimize_state(self, state: ResearchState) -> ResearchState:
        """Execute state compaction over ResearchState."""
        scraped = state.get("scraped_contents", [])
        if not scraped:
            return {}

        initial_raw_bytes = sum(len(doc.get("content", "") or "") for doc in scraped)
        compacted = self.compact_scraped_contents(scraped)
        compacted_bytes = sum(len(doc.get("content_summary", "") or "") for doc in compacted)

        bytes_saved = max(0, initial_raw_bytes - compacted_bytes)
        savings_percent = round((bytes_saved / max(1, initial_raw_bytes)) * 100, 1)

        # Capture the validated corpus BEFORE the raw text is dropped. This is
        # the first place the page text is stripped, so it is the last place
        # the corpus can be taken from. The evaluation node measures
        # hallucination against this; without it the measurement is skipped.
        corpus = build_corpus(scraped)

        log.info(
            "State compaction complete",
            doc_count=len(scraped),
            initial_raw_bytes=initial_raw_bytes,
            compacted_bytes=compacted_bytes,
            bytes_saved=bytes_saved,
            savings_percent=f"{savings_percent}%",
            corpus_chars=len(corpus),
        )

        return {"scraped_contents": compacted, "evidence_corpus": corpus}


# The corpus is a bounded sample of the retrieved text, not the raw blobs: the
# point of compaction is to keep raw document text out of the remaining
# pipeline, and this sample is small enough not to undo that.
_MAX_CORPUS_CHARS = 200_000


def build_corpus(scraped: list[dict[str, Any]]) -> str:
    """Concatenate the scraped page text into a bounded corpus sample."""
    parts: list[str] = []
    total = 0
    for page in scraped:
        if not isinstance(page, dict):
            continue
        content = str(page.get("content") or "")
        if not content:
            continue
        parts.append(content)
        total += len(content)
        if total >= _MAX_CORPUS_CHARS:
            break
    return "\n".join(parts)[:_MAX_CORPUS_CHARS]


async def state_compressor_node(state: ResearchState) -> ResearchState:
    """LangGraph node dispatcher for state memory compaction."""
    optimizer = StateTokenOptimizer()
    return await optimizer.optimize_state(state)
