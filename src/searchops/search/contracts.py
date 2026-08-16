"""
Search Data Contracts and Provider Abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from pydantic import Field

from searchops.shared.contracts.base import BaseSchema
from searchops.search.domain.models import SearchCapability, NormalizedSearchResult, SearchProfile

class SearchResultItem(NormalizedSearchResult):
    """Individual search result item (backward compatible wrapper)."""
    published_date: str | None = None


class SearchQuery(BaseSchema):
    """Search request model."""

    query: str
    max_results: int = 10
    search_depth: str = "standard"  # 'basic' or 'advanced'
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    required_capabilities: list[SearchCapability] = Field(default_factory=list)
    profile: SearchProfile = SearchProfile.FAST


class ISearchProvider(ABC):
    """Abstract Port for Search Engine Providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name identifier."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> set[SearchCapability]:
        """Set of supported capabilities."""
        ...

    @property
    @abstractmethod
    def cost_per_query(self) -> float:
        """Cost per standard query execution in USD."""
        ...

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform search and return normalized results."""
        ...
