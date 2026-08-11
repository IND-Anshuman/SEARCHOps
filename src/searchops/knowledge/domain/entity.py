"""
Knowledge Graph Domain Models: Entity and Relation.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import Field

from searchops.shared.domain.entity import BaseEntity
from searchops.shared.domain.value_object import BaseValueObject
from searchops.typing.aliases import EntityId, NodeId


def slugify(text: str) -> str:
    """Convert string into clean snake_case slug for canonical indexing."""
    clean = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "_", clean)


class EntityType(BaseValueObject):
    """Knowledge Graph Entity Type (e.g., Technology, Organization, Person, Concept)."""

    name: str


class KGEntity(BaseEntity):
    """Knowledge Graph Node Entity with canonical deduplication support."""

    name: str
    entity_type: str
    description: str = ""
    canonical_id: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    embedding: list[float] | None = None

    def model_post_init(self, __context: Any) -> None:
        """Compute canonical_id if omitted."""
        if not self.canonical_id:
            slug_type = slugify(self.entity_type) or "concept"
            slug_name = slugify(self.name) or "unknown"
            self.canonical_id = f"{slug_type}:{slug_name}"


class KGRelation(BaseEntity):
    """Knowledge Graph Edge Relation between two entities."""

    source_id: EntityId
    target_id: EntityId
    source_canonical_id: str = ""
    target_canonical_id: str = ""
    relation_type: str
    description: str = ""
    weight: float = Field(default=1.0, ge=0.0)
    properties: dict[str, Any] = Field(default_factory=dict)

