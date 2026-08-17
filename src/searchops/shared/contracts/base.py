"""
Base Pydantic schemas for all API contracts.

All request and response schemas should inherit from these base classes
to ensure consistent serialization, validation, and documentation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    """Root schema with consistent config for all platform schemas."""
    
    model_config = ConfigDict(
        # Use enum values (not names) in JSON
        use_enum_values=True,
        # Populate models from ORM objects
        from_attributes=True,
        # Strict validation (no coercions)
        strict=False,
        # Validate on assignment
        validate_assignment=True,
        # Allow extra fields to be stripped (not error)
        extra="ignore",
        # JSON serialization: use Python types not Pydantic types
        arbitrary_types_allowed=True,
    )


class BaseRequest(BaseSchema):
    """Base class for all API request schemas."""
    
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Client-generated request identifier for idempotency",
    )


class BaseResponse(BaseSchema):
    """Base class for all API response schemas."""
    
    request_id: str | None = Field(
        default=None,
        description="Echo of the client request ID",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the response was generated",
    )
