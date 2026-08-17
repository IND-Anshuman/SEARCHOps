"""
Pagination schemas.

The platform supports two pagination strategies:
1. Offset-based (page/size) — for sorted, stable datasets
2. Cursor-based — for real-time feeds and large datasets (preferred)
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import Field

from searchops.shared.contracts.base import BaseSchema

T = TypeVar("T")


class PaginationParams(BaseSchema):
    """Offset-based pagination query parameters."""
    
    page: int = Field(default=1, ge=1, description="1-indexed page number")
    size: int = Field(default=20, ge=1, le=100, description="Items per page")
    
    @property
    def offset(self) -> int:
        """Calculate the SQL OFFSET value."""
        return (self.page - 1) * self.size
    
    @property
    def limit(self) -> int:
        """Alias for size for use in SQL LIMIT."""
        return self.size


class PaginatedResponse(BaseSchema, Generic[T]):
    """Generic paginated response wrapper."""
    
    items: list[T] = Field(description="Page items")
    total: int = Field(description="Total number of items matching the query")
    page: int = Field(description="Current page number (1-indexed)")
    size: int = Field(description="Items per page")
    pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="True if there are more pages")
    has_previous: bool = Field(description="True if there are previous pages")
    
    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        params: PaginationParams,
    ) -> PaginatedResponse[T]:
        """Factory method for creating a paginated response."""
        pages = max(1, (total + params.size - 1) // params.size)
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=pages,
            has_next=params.page < pages,
            has_previous=params.page > 1,
        )


class CursorPaginatedResponse(BaseSchema, Generic[T]):
    """Cursor-based paginated response for real-time feeds."""
    
    items: list[T] = Field(description="Page items")
    cursor: str | None = Field(
        default=None,
        description="Opaque cursor to fetch the next page",
    )
    has_next: bool = Field(description="True if there are more items")
    count: int = Field(description="Number of items in this page")
