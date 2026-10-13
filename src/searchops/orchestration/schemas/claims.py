"""
Intermediate Pydantic schemas for LLM claim extraction.

These mirror ``searchops/knowledge/schemas/extraction.py``: the LLM returns
raw JSON which we parse into these "candidate" models and then map onto the
domain models in ``searchops/orchestration/evidence.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedCitation(BaseModel):
    """A source the LLM attributes to a claim."""

    url: str = Field(..., description="https URL of the citing source")
    title: str = Field(default="", description="Page/document title")
    date: str = Field(default="", description="ISO-8601 publication date, if known")
    author: str = Field(default="", description="Author, if known")
    excerpt: str = Field(default="", max_length=280, description="short verbatim supporting span <=280 chars")


class ExtractedClaim(BaseModel):
    """A single factual claim the LLM extracted from the document."""

    text: str = Field(..., description="One standalone factual statement")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Optional and null-tolerant: models routinely emit ``"citations": null``
    # or omit the key entirely, and a claim without citations is still
    # evidence — rejecting the whole document over it loses real signal.
    citations: list[ExtractedCitation] | None = Field(default=None, description="Sources attributed to the claim")


class ClaimsResult(BaseModel):
    """Container model for claim-extraction LLM output."""

    claims: list[ExtractedClaim] = Field(default_factory=list)
