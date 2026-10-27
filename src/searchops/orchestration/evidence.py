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
    # The passage of the page the excerpt was found in, and whether the
    # excerpt was confirmed verbatim against the page text (not just claimed
    # by the model).
    passage_id: str = ""
    quote_verified: bool = False


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
    # Filled by the verifier from source passages: which sources corroborate
    # or contradict the claim, and a one-line reason for the verdict.
    supporting_urls: list[str] = Field(default_factory=list)
    contradicting_urls: list[str] = Field(default_factory=list)
    verification_note: str = ""

    @property
    def grounded(self) -> bool:
        """True when at least one citation's quote was found in its page."""
        return any(c.quote_verified for c in self.citations)


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
    """LangGraph state reducer: merge claim deltas, keyed on ``claim.id``.

    Order of first appearance is preserved, but the **latest** version of a
    claim wins. Nodes such as the verifier return updated copies of existing
    claims (same id, new status); a first-wins merge silently discarded every
    one of those updates, so no claim could ever leave ``unverified``.
    """
    merged: dict[str, Claim] = {}
    for claim in (left or []) + (right or []):
        merged[claim.id] = claim  # dict keeps first-insertion order on overwrite
    return list(merged.values())
