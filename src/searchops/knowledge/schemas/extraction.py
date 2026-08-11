"""
Reusable Knowledge Graph extraction Pydantic schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedEntity(BaseModel):
    """Domain model representing a single extracted entity candidate."""
    name: str = Field(..., description="Canonical entity name")
    type: str = Field(default="Concept", description="Technology | Organization | Concept")
    description: str = Field(default="", description="Short description of the entity")


class ExtractedRelation(BaseModel):
    """Domain model representing a relationship between two entities."""
    source: str = Field(..., description="Source entity name")
    target: str = Field(..., description="Target entity name")
    type: str = Field(default="RELATED_TO", description="USES | DEPENDS_ON | CREATED_BY | RELATED_TO")
    description: str = Field(default="", description="Relationship description")


class ExtractionResult(BaseModel):
    """Container model for batch entity and relationship extraction results."""
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
