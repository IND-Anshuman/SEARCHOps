"""Shared API contracts — request/response schemas used across bounded contexts."""

from searchops.shared.contracts.base import BaseRequest, BaseResponse, BaseSchema
from searchops.shared.contracts.errors import ErrorDetail, ErrorResponse, FieldError
from searchops.shared.contracts.health import ComponentHealth, HealthResponse
from searchops.shared.contracts.pagination import (
    CursorPaginatedResponse,
    PaginatedResponse,
    PaginationParams,
)

__all__ = [
    "BaseRequest",
    "BaseResponse",
    "BaseSchema",
    "ComponentHealth",
    "CursorPaginatedResponse",
    "ErrorDetail",
    "ErrorResponse",
    "FieldError",
    "HealthResponse",
    "PaginatedResponse",
    "PaginationParams",
]
