"""
Typed Evidence Layer for the SEARCHOps Deep-Research pipeline.

Replaces implicit "truncated markdown in state" with structured evidence so
every downstream prompt operates on typed ``Claim`` objects that carry their
own inline ``Citation`` metadata. This is the foundation for per-claim
grounded citation in the report writer, per-claim verification in the
verifier, and coverage-gap analysis in the critic.

Design notes:
- All models are pydantic v2 so prompt builders can read fields safely
  (``claim.citations[0].url``) rather than reaching into raw dicts.
- ``Citation.excerpt`` is capped at 280 chars to bound prompt tokens.
- ``dedupe_claims`` is a LangGraph state reducer (mirrors ``dedupe_entities``
  in ``searchops/orchestration/states/research_state.py``): it merges the
  incoming claim delta into the accumulated list, keyed on ``claim.id``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A single verifiable source for a claim, enriched with metadata."""

    url: str
    title: str = ""
    date: str = ""  # ISO-8601 best-effort
    author: str = ""
    excerpt: str = Field(default="", description="short verbatim span", max_length=280)


class EvidenceSpan(BaseModel):
    """A byte/char span within a scraped document that supports a claim."""

    doc_id: str
    start: int
    end: int


class Claim(BaseModel):
    """A single factual statement extracted from evidence, with citations."""

    id: str
    text: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    status: Literal["unverified", "supported", "contested", "refuted"] = "unverified"


class EvidenceGap(BaseModel):
    """A coverage gap the critic identified; may carry a follow-up sub-query."""

    description: str
    target_query: str = ""
    priority: int = 1


class EvidenceStore(BaseModel):
    """Aggregate of claims and gaps carried across the research loop."""

    claims: list[Claim] = Field(default_factory=list)
    gaps: list[EvidenceGap] = Field(default_factory=list)


def dedupe_claims(left: list[Claim] | None, right: list[Claim] | None) -> list[Claim]:
    """LangGraph state reducer: merge claim deltas, dedupe by ``claim.id``.

    First occurrence of any given id wins; order of first appearance is
    preserved. Mirrors ``dedupe_entities`` in the research state module.
    """
    combined = (left or []) + (right or [])
    seen: dict[str, Claim] = {}
    for claim in combined:
        if claim.id not in seen:
            seen[claim.id] = claim
    return list(seen.values())
