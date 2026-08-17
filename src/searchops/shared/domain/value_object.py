"""
Value Object base class for DDD.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class BaseValueObject(BaseModel):
    """
    Base class for all domain value objects.
    
    Value objects have no identity, only value equality. They are immutable.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    def __eq__(self, other: Any) -> bool:
        """Value objects are equal if all their fields match and they are of the same type."""
        if isinstance(other, type(self)):
            return self.model_dump() == other.model_dump()
        return False

    def __hash__(self) -> int:
        """Hash based on a hash of all field values."""
        return hash(tuple(self.model_dump().items()))
