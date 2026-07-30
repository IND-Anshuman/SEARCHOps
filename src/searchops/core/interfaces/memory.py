"""
Memory system protocols.

Four independent memory systems are available:
  1. ICache          — short-lived key-value (Redis)
  2. IMemoryStore    — structured document storage (PostgreSQL)
  3. IVectorStore    — embedding similarity search (Qdrant)
  4. IGraphStore     — knowledge graph (Neo4j)
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ICache(Protocol):
    """Short-lived key-value cache (e.g., Redis)."""
    
    async def get(self, key: str) -> Any | None:
        """Retrieve a value by key. Returns None if not found or expired."""
        ...
    
    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store a value with an optional TTL."""
        ...
    
    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        ...
    
    async def exists(self, key: str) -> bool:
        """Check if a key exists without retrieving the value."""
        ...
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter. Returns the new value."""
        ...
    
    async def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set a TTL on an existing key. Returns True if key was found."""
        ...
    
    async def flush_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern. Returns count deleted."""
        ...


@runtime_checkable
class IMemoryStore(Protocol):
    """Structured document store for workflow and execution memory (PostgreSQL)."""
    
    async def store(
        self,
        key: str,
        value: dict[str, Any],
        *,
        namespace: str = "default",
        ttl_seconds: int | None = None,
    ) -> None:
        """Store a document under the given key in a namespace."""
        ...
    
    async def retrieve(
        self, key: str, *, namespace: str = "default"
    ) -> dict[str, Any] | None:
        """Retrieve a document by key from a namespace."""
        ...
    
    async def list_keys(
        self,
        *,
        namespace: str = "default",
        prefix: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        """List keys in a namespace with optional prefix filter."""
        ...
    
    async def delete(self, key: str, *, namespace: str = "default") -> bool:
        """Delete a document. Returns True if it existed."""
        ...
