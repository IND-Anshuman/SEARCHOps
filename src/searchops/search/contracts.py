"""
Search Data Contracts and Provider Abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import Field

from searchops.search.domain.models import NormalizedSearchResult, SearchCapability, SearchProfile
from searchops.shared.contracts.base import BaseSchema

#: Default provider priority when a provider declares none. Every built-in
#: provider previously fell back to this implicitly, which made routing order
#: depend on module-name alphabetical order rather than on any declared intent.
DEFAULT_PRIORITY = 100


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
    raw_metadata: dict[str, Any] = Field(default_factory=dict)  # Extra per-query context (e.g. country, job_id)


class ISearchProvider(ABC):
    """Abstract Port for Search Engine Providers.

    Two declarative attributes drive routing, both read from the provider
    itself rather than from provider-name strings anywhere in the framework:

    - :attr:`requires_credentials` — whether the provider needs an API key.
      Key-free providers are tried first so the platform works with no keys.
    - :attr:`priority` — ordering within a tier (lower = tried first).
      Defaults to :data:`DEFAULT_PRIORITY`.
    """

    #: Priority ordering within a tier. Lower runs first.
    priority: int = DEFAULT_PRIORITY

    #: True when the provider cannot run without an external API key.
    requires_credentials: bool = True

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

    @property
    def is_key_free(self) -> bool:
        """True when this provider runs with no external credentials.

        Prefer these providers: they keep the internal framework self-sufficient.
        """
        return not self.requires_credentials

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchResultItem]:
        """Perform search and return normalized results."""
        ...
