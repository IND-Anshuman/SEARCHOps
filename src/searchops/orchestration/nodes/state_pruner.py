"""
State Pruner & Memory Compaction node: runs once, before report synthesis.

Raw page text never travels further than extraction: by this point it lives
only in the passage store. This node captures the run's *validated corpus*
(the passage text, every loop's) for the evaluation node, which measures
hallucination against it, and blanks any page text still left on the
``scraped_contents`` records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from searchops.orchestration.passages import passages_from_state

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

# The corpus is bounded so it cannot blow up the state of the node that reads it.
# Large enough to hold every passage of a deep run; the hallucination check is
# a substring scan, so size costs little and truncation costs accuracy.
_MAX_CORPUS_CHARS = 2_000_000


async def state_pruner_node(state: ResearchState) -> ResearchState:
    """Capture the validated corpus and drop leftover raw page text."""
    scraped = state.get("scraped_contents", []) or []
    passages = passages_from_state(state.get("passages", []))
    cited = {
        getattr(c, "passage_id", "")
        for claim in state.get("evidence_claims", []) or []
        for c in getattr(claim, "citations", []) or []
    }
    corpus = build_corpus(passages, cited) or state.get("evidence_corpus", "") or _legacy_corpus(scraped)
    log.info(
        "State pruner executing memory compaction",
        pages=len(scraped),
        passages=len(passages),
        corpus_chars=len(corpus),
    )
    compacted = [{"url": d.get("url", ""), "title": d.get("title", ""), "content": ""} for d in scraped]
    return {"scraped_contents": compacted, "round_documents": [], "evidence_corpus": corpus}


def build_corpus(passages: list[Any], cited: set[str] | None = None) -> str:
    """Concatenate passage text, across every loop, into a bounded corpus.

    Passages that claims cite go first, so if the bound is ever reached the
    text the claims rest on is never the part that was cut.
    """
    cited = cited or set()
    ordered = sorted(passages, key=lambda p: p.id not in cited)
    parts: list[str] = []
    total = 0
    for p in ordered:
        parts.append(p.text)
        total += len(p.text)
        if total >= _MAX_CORPUS_CHARS:
            break
    return "\n\n".join(parts)[:_MAX_CORPUS_CHARS]


def _legacy_corpus(scraped: list[dict[str, Any]]) -> str:
    """Corpus from page text on the records themselves (no passage store)."""
    text = "\n".join(str(d.get("content") or "") for d in scraped if isinstance(d, dict))
    return text[:_MAX_CORPUS_CHARS]
