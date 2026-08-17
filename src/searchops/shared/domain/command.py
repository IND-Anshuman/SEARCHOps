"""
Command base class.

Commands represent intentions — requests to change system state.
They are mutable (unlike events) and carry input validated at the API boundary.

Naming convention: imperative verb (StartResearch, CancelResearch, etc.)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Command(BaseModel):
    """Base class for all platform commands.
    
    Attributes:
        command_id: Unique command identifier.
        issued_at: UTC timestamp when the command was issued.
        issued_by: User or system that issued the command.
        correlation_id: Cross-service correlation identifier.
        metadata: Arbitrary command metadata.
    """
    
    model_config = ConfigDict(frozen=True)
    
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    issued_by: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
