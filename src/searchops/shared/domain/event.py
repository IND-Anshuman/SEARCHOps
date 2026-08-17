"""
DomainEvent base class.

Domain events represent facts that have already happened in the domain.
They are immutable once created and carry all data needed for handling.

Naming convention: past-tense verb (ResearchCompleted, EntityExtracted, etc.)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DomainEvent(BaseModel):
    """Immutable base class for all domain events.
    
    Attributes:
        event_id: Unique event identifier.
        event_type: Discriminator string (defaults to class name).
        occurred_at: UTC timestamp when the event occurred.
        correlation_id: Cross-service tracing identifier.
        causation_id: ID of the event or command that caused this event.
        metadata: Arbitrary event metadata.
    """
    
    model_config = ConfigDict(frozen=True)
    
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = Field(default="")
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    def model_post_init(self, __context: Any) -> None:
        """Set event_type to the class name if not explicitly provided."""
        if not self.event_type:
            # Use object.__setattr__ because the model is frozen
            object.__setattr__(self, "event_type", self.__class__.__name__)
    
    def with_correlation(self, correlation_id: str) -> DomainEvent:
        """Return a copy of this event with the correlation ID set."""
        return self.model_copy(update={"correlation_id": correlation_id})
