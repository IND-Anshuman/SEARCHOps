"""Unified extraction schema: claims + entities + relations in one LLM response.

This replaces the previous two-call pattern (extract_claims + extract_knowledge)
with a single call that returns all three evidence types. The LLM returns one
JSON object with three arrays, cutting extraction calls in half.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class UnifiedCitation(BaseModel):
    """A source attributed to a claim or relation."""

    url: str = Field(default="", description="https URL of the citing source")
    title: str = Field(default="", description="Page/document title")
    excerpt: str = Field(default="", description="short verbatim supporting span")

    @field_validator("excerpt", mode="before")
    @classmethod
    def _cap_excerpt(cls, v: object) -> str:
        # Truncate rather than reject: one long excerpt used to fail schema
        # validation and discard every claim extracted from the document.
        return str(v or "")[:280]


class UnifiedClaim(BaseModel):
    """A single factual claim extracted from the document."""

    text: str = Field(..., description="One standalone factual statement")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    citations: list[UnifiedCitation] = Field(default_factory=list)
    # Passage label (e.g. "P3") the claim was read from, and the verbatim
    # sentence that states it. Checked against the page text downstream.
    passage: str = ""
    quote: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: object) -> float:
        try:
            return min(max(float(v), 0.0), 1.0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5

    @field_validator("passage", "quote", mode="before")
    @classmethod
    def _str_or_empty(cls, v: object) -> str:
        return "" if v is None else str(v)

    @field_validator("citations", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return v if v is not None else []


class UnifiedEntity(BaseModel):
    """A key concept, technology, or organization."""

    name: str = Field(..., description="Canonical entity name")
    type: str = Field(default="Concept", description="Technology | Organization | Concept")
    description: str = Field(default="", description="Short description")


class UnifiedRelation(BaseModel):
    """A relationship between two entities."""

    source: str = Field(..., description="Source entity name")
    target: str = Field(..., description="Target entity name")
    type: str = Field(default="RELATED_TO", description="USES | DEPENDS_ON | CREATED_BY | RELATED_TO")
    description: str = Field(default="", description="Relationship description")


class UnifiedExtractionResult(BaseModel):
    """Container for unified extraction LLM output."""

    claims: list[UnifiedClaim] = Field(default_factory=list)
    entities: list[UnifiedEntity] = Field(default_factory=list)
    relations: list[UnifiedRelation] = Field(default_factory=list)
