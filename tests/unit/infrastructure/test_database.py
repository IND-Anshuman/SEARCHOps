"""
Unit and Integration tests for Database Layer, Repositories, and Unit of Work.
"""

from __future__ import annotations

from typing import Any
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from searchops.infrastructure.database.base import BaseORM, TimestampMixin
from searchops.infrastructure.database.repositories.base import SQLAlchemyRepository
from searchops.infrastructure.database.unit_of_work import UnitOfWork


# ── Sample Model & Domain Class for Testing ─────────────────────────────────

class DummyORM(BaseORM, TimestampMixin):
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class DummyDomain:
    def __init__(self, id: str, name: str) -> None:
        self.id = id
        self.name = name


class DummyRepository(SQLAlchemyRepository[DummyORM, DummyDomain, str]):
    def _to_domain(self, model: DummyORM) -> DummyDomain:
        return DummyDomain(id=model.id, name=model.name)

    def _to_orm(self, entity: DummyDomain) -> DummyORM:
        return DummyORM(id=entity.id, name=entity.name)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_db_session() -> AsyncSession:
    """In-memory SQLite async engine and session for fast isolated testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(BaseORM.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_repository_crud_operations(async_db_session: AsyncSession):
    repo = DummyRepository(async_db_session, DummyORM)

    # 1. Add
    entity = DummyDomain(id="1", name="Test Item")
    added = await repo.add(entity)
    assert added.id == "1"
    assert added.name == "Test Item"

    # 2. Get by ID
    fetched = await repo.get_by_id("1")
    assert fetched is not None
    assert fetched.name == "Test Item"

    # 3. Count
    count = await repo.count()
    assert count == 1

    # 4. Update
    entity.name = "Updated Item"
    updated = await repo.update(entity)
    assert updated.name == "Updated Item"

    # 5. Get All
    items = await repo.get_all()
    assert len(items) == 1
    assert items[0].name == "Updated Item"

    # 6. Delete
    deleted = await repo.delete("1")
    assert deleted is True
    assert await repo.get_by_id("1") is None
    assert await repo.count() == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unit_of_work_commit_and_rollback(async_db_session: AsyncSession):
    uow = UnitOfWork(session=async_db_session)
    repo = DummyRepository(async_db_session, DummyORM)

    # Test commit via context manager
    async with uow:
        await repo.add(DummyDomain(id="uow-1", name="UoW Item"))

    fetched = await repo.get_by_id("uow-1")
    assert fetched is not None

    # Test rollback on exception
    with pytest.raises(ValueError):
        async with uow:
            await repo.add(DummyDomain(id="uow-2", name="Should Rollback"))
            raise ValueError("Forced error")

    fetched_failed = await repo.get_by_id("uow-2")
    assert fetched_failed is None
