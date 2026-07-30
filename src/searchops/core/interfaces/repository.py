"""
Generic Repository Protocol (port).

Follows the Repository Pattern from DDD. Concrete implementations live
in infrastructure/. The domain never sees a specific ORM or DB driver.
"""
from __future__ import annotations

from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from searchops.typing.aliases import EntityId

T = TypeVar("T")
ID = TypeVar("ID")


@runtime_checkable
class IReadRepository(Protocol[T, ID]):
    """Read-only repository contract."""
    
    async def get_by_id(self, entity_id: ID) -> T | None:
        """Retrieve an entity by its identifier. Returns None if not found."""
        ...
    
    async def get_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
    ) -> list[T]:
        """Retrieve a paginated list of entities with optional filters."""
        ...
    
    async def exists(self, entity_id: ID) -> bool:
        """Check whether an entity with the given ID exists."""
        ...
    
    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """Count entities matching the given filters."""
        ...


@runtime_checkable
class IRepository(IReadRepository[T, ID], Protocol[T, ID]):
    """Full CRUD repository contract."""
    
    async def save(self, entity: T) -> T:
        """Persist a new entity or update an existing one. Returns the saved entity."""
        ...
    
    async def save_many(self, entities: list[T]) -> list[T]:
        """Bulk persist entities. Returns the saved entities."""
        ...
    
    async def delete(self, entity_id: ID) -> bool:
        """Delete an entity by ID. Returns True if deleted, False if not found."""
        ...
    
    async def delete_many(self, entity_ids: list[ID]) -> int:
        """Bulk delete entities. Returns the count of deleted entities."""
        ...
