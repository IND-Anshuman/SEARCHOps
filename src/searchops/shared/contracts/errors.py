"""
Error response schemas.

Follows RFC 7807 (Problem Details for HTTP APIs).
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from searchops.shared.contracts.base import BaseSchema


class FieldError(BaseSchema):
    """A validation error on a specific field."""
    
    field: str = Field(description="Dot-separated field path (e.g., 'user.email')")
    message: str = Field(description="Human-readable error message")
    code: str = Field(description="Machine-readable error code")


class ErrorDetail(BaseSchema):
    """Detailed error information (RFC 7807 Problem Detail)."""
    
    type: str = Field(description="URI reference identifying the problem type")
    title: str = Field(description="Short, human-readable summary")
    status: int = Field(description="HTTP status code")
    detail: str = Field(description="Human-readable explanation specific to this occurrence")
    instance: str = Field(description="URI reference identifying the specific occurrence")
    error_code: str | None = Field(default=None, description="Platform error code")
    correlation_id: str | None = Field(default=None)
    context: dict[str, Any] = Field(default_factory=dict)
    field_errors: list[FieldError] = Field(default_factory=list)


class ErrorResponse(BaseSchema):
    """Standard error response envelope."""
    
    error: ErrorDetail
