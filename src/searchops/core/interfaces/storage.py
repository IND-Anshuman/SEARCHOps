"""
Storage Abstraction Layer: Domain Interfaces for Vector, Graph, Cache, and Checkpoint Stores.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from searchops.knowledge.domain.entity import KGEntity, KGRelation


class IVectorStore(ABC):
    """Abstract Port for Vector Databases (Qdrant, PgVector, Milvus)."""

    @abstractmethod
    async def init_collection(self, collection_name: str, vector_size: int = 1536) -> None:
        """Initialize vector collection if it does not exist."""
        ...

    @abstractmethod
    async def upsert_chunks(self, collection_name: str, points: list[Any]) -> None:
        """Upsert vector points into collection."""
        ...

    @abstractmethod
    async def search_similar(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Perform dense vector similarity search."""
        ...


class IGraphStore(ABC):
    """Abstract Port for Knowledge Graph Databases (Neo4j, Memgraph)."""

    @abstractmethod
    async def init_indexes(self) -> None:
        """Initialize uniqueness constraints and indexes."""
        ...

    @abstractmethod
    async def upsert_entity(self, entity: Any) -> None:
        """Upsert a single Knowledge Graph entity."""
        ...

    @abstractmethod
    async def upsert_entities_batch(self, entities: list[Any]) -> None:
        """Perform high-performance batched upsert of entities."""
        ...

    @abstractmethod
    async def upsert_relation(self, relation: Any) -> None:
        """Upsert a single Knowledge Graph relation."""
        ...

    @abstractmethod
    async def upsert_relations_batch(self, relations: list[Any]) -> None:
        """Perform high-performance batched UNWIND upsert of relations."""
        ...


class ICacheStore(ABC):
    """Abstract Port for Key-Value Caches (Redis, Memcached)."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Retrieve value by key."""
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        """Set key-value pair with optional TTL."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete key."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if key exists."""
        ...
