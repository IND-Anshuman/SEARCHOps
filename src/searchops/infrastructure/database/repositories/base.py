"""
Generic Async SQLAlchemy Repository implementation.

Implements `IRepository[T, ID]` from `core.interfaces.repository`.
Maps between Domain Entities and SQLAlchemy ORM models cleanly.
"""

from __future__ import annotations

from typing import Any, Generic, Sequence, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from searchops.core.exceptions.domain import EntityNotFoundError
from searchops.core.exceptions.infrastructure import DatabaseError
from searchops.core.interfaces.repository import IRepository
from searchops.infrastructure.database.base import BaseORM

ORMModel = TypeVar("ORMModel", bound=BaseORM)
DomainEntity = TypeVar("DomainEntity")
ID = TypeVar("ID")


class SQLAlchemyRepository(IRepository[DomainEntity, ID], Generic[ORMModel, DomainEntity, ID]):
    """Generic async repository backed by SQLAlchemy 2.0."""

    def __init__(self, session: AsyncSession, model_cls: type[ORMModel]) -> None:
        self.session = session
        self.model_cls = model_cls

    def _to_domain(self, model: ORMModel) -> DomainEntity:
        """Map ORM model to Domain entity. Must be overridden by concrete repositories."""
        raise NotImplementedError

    def _to_orm(self, entity: DomainEntity) -> ORMModel:
        """Map Domain entity to ORM model. Must be overridden by concrete repositories."""
        raise NotImplementedError

    async def get_by_id(self, entity_id: ID) -> DomainEntity | None:
        """Fetch entity by primary key."""
        try:
            stmt = select(self.model_cls).where(getattr(self.model_cls, "id") == entity_id)
            result = await self.session.execute(stmt)
            model = result.scalar_one_or_none()
            return self._to_domain(model) if model else None
        except Exception as exc:
            raise DatabaseError(f"Error fetching {self.model_cls.__name__} by id", cause=exc) from exc

    async def get_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
    ) -> list[DomainEntity]:
        """Fetch multiple entities with pagination and optional filtering."""
        try:
            stmt = select(self.model_cls)
            if filters:
                for key, value in filters.items():
                    if hasattr(self.model_cls, key):
                        stmt = stmt.where(getattr(self.model_cls, key) == value)
            stmt = stmt.offset(offset).limit(limit)
            result = await self.session.execute(stmt)
            models = result.scalars().all()
            return [self._to_domain(m) for m in models]
        except Exception as exc:
            raise DatabaseError(f"Error fetching {self.model_cls.__name__} list", cause=exc) from exc

    async def add(self, entity: DomainEntity) -> DomainEntity:
        """Add a new entity."""
        try:
            model = self._to_orm(entity)
            self.session.add(model)
            await self.session.flush()
            return self._to_domain(model)
        except Exception as exc:
            raise DatabaseError(f"Error adding {self.model_cls.__name__}", cause=exc) from exc

    async def update(self, entity: DomainEntity) -> DomainEntity:
        """Update an existing entity."""
        try:
            model = self._to_orm(entity)
            merged = await self.session.merge(model)
            await self.session.flush()
            return self._to_domain(merged)
        except Exception as exc:
            raise DatabaseError(f"Error updating {self.model_cls.__name__}", cause=exc) from exc

    async def delete(self, entity_id: ID) -> bool:
        """Delete an entity by id."""
        try:
            stmt = delete(self.model_cls).where(getattr(self.model_cls, "id") == entity_id)
            result = await self.session.execute(stmt)
            return bool(result.rowcount > 0)
        except Exception as exc:
            raise DatabaseError(f"Error deleting {self.model_cls.__name__}", cause=exc) from exc

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """Count entities matching filters."""
        try:
            stmt = select(func.count()).select_from(self.model_cls)
            if filters:
                for key, value in filters.items():
                    if hasattr(self.model_cls, key):
                        stmt = stmt.where(getattr(self.model_cls, key) == value)
            result = await self.session.execute(stmt)
            return result.scalar_one() or 0
        except Exception as exc:
            raise DatabaseError(f"Error counting {self.model_cls.__name__}", cause=exc) from exc
