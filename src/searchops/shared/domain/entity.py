"""
Base Entity for DDD.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import shortuuid
from pydantic import BaseModel, ConfigDict, Field


def _generate_id() -> str:
    """Generate a short UUID string."""
    return shortuuid.uuid()


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class BaseEntity(BaseModel):
    """
    Base class for all domain entities.
    
    Entities have identity and mutability. Equality is based purely on ID.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    id: str = Field(default_factory=_generate_id)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    version: int = Field(default=0)

    def __eq__(self, other: Any) -> bool:
        """Entities are equal if their IDs match and they are of the same type."""
        if isinstance(other, type(self)):
            return self.id == other.id
        return False

    def __hash__(self) -> int:
        """Hash based on entity ID."""
        return hash(self.id)

    def touch(self) -> None:
        """Update the updated_at timestamp to now."""
        self.updated_at = _now()

    def increment_version(self) -> None:
        """Increment the version for optimistic concurrency."""
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of the entity."""
        return self.model_dump()
