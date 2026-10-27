"""Enrichment pass schema: one LLM call producing four study artifacts.

Why one call
------------
Before this module, the study aid features were three independent model calls
reading the same bounded excerpt of the report:

* ``study-guide`` — 1 call, then a second call to fill in answers
* ``faq`` — 3 calls (it re-invokes ``build_study_guide``)
* ``glossary`` — 0 calls, but it surfaced the extractor's *"short description"*
  field, so every definition was a one-liner by construction
* ``next-steps`` — 0 calls, but it replayed the critic's gaps verbatim, so the
  reader got a gap sentence with no context about why it matters

All four were shallow for the same reason: each was asked for a thin fragment,
from a slice of the report, in isolation. One call over the accumulated corpus
can produce all four at real depth — the model already holds the whole context,
so a richer definition costs it nothing extra in prompt tokens.

Depth contract
--------------
The prompt asks for per-field length floors. The parser enforces them and marks
a field ``thin`` when the model returned less, rather than silently shipping a
fragment. A ``thin`` flag is an honest signal, not a silent failure.

Honesty contract (unchanged from the rest of the product)
---------------------------------------------------------
Nothing here may invent content. A field the corpus cannot support is returned
empty with a reason, never filled from model memory. An empty study guide that
pretends to be complete is worse than an honest empty one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EnrichedTerm(BaseModel):
    """A glossary entry with a definition that says something.

    ``definition`` is required and must meet a length floor; a one-liner is
    exactly the failure this module exists to fix.
    """

    term: str = Field(..., description="Canonical name of the term")
    entity_type: str = Field(default="Concept", description="Type of the entity")
    definition: str = Field(..., description="2-4 sentences: what it is, why it matters here, how it relates")
    related: list[str] = Field(default_factory=list, description="Names of related terms")

    @field_validator("related", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return v if v is not None else []


class EnrichedQuestion(BaseModel):
    """One study question with a real answer and the section it came from."""

    question: str = Field(..., description="The question")
    answer: str = Field(..., description="3-6 sentences, drawn only from the report")
    section: str = Field(default="", description="Section the question came from")
    tests: Literal["concept", "comparison", "limitation", "mechanism", "general"] = Field(
        default="general",
        description="What kind of understanding this question checks",
    )


class EnrichedGap(BaseModel):
    """A coverage gap with the context needed to act on it."""

    gap: str = Field(..., description="What the run could not establish")
    why_it_matters: str = Field(..., description="Why this gap limits the reader's understanding")
    suggested_query: str = Field(..., description="A concrete search query that would close this")
    prerequisites: list[str] = Field(default_factory=list, description="Terms to understand first")
    priority: int = Field(default=1, ge=1, le=5, description="1 = critical, 5 = peripheral")

    @field_validator("prerequisites", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return v if v is not None else []


class EnrichedFaq(BaseModel):
    """One FAQ entry."""

    q: str = Field(..., description="The question a reader would actually ask")
    a: str = Field(..., description="A direct answer drawn only from the report")


class EnrichmentResult(BaseModel):
    """The four study artifacts, produced in one model call."""

    glossary: list[EnrichedTerm] = Field(default_factory=list)
    study_guide: list[EnrichedQuestion] = Field(default_factory=list)
    next_steps: list[EnrichedGap] = Field(default_factory=list)
    faq: list[EnrichedFaq] = Field(default_factory=list)
    note: str = Field(default="", description="Honest note about coverage limits")

    # Field-level depth verdicts. A field the model filled with fragments is
    # marked thin; the caller decides whether to surface that.
    thin_fields: list[str] = Field(default_factory=list)

    @field_validator("glossary", "study_guide", "next_steps", "faq", "thin_fields", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return v if v is not None else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "glossary": [g.model_dump() for g in self.glossary],
            "study_guide": [q.model_dump() for q in self.study_guide],
            "next_steps": [g.model_dump() for g in self.next_steps],
            "faq": [f.model_dump() for f in self.faq],
            "note": self.note,
            "thin_fields": list(self.thin_fields),
        }
