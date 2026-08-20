"""
Unit tests for database connection pooling, sessionmaker, and UnitOfWork lifecycle.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from searchops.infrastructure.database.connection import get_engine, get_sessionmaker, close_engine, get_db_session
from searchops.infrastructure.database.unit_of_work import UnitOfWork


@pytest.mark.unit
@pytest.mark.asyncio
async def test_database_engine_and_session():
    engine = get_engine()
    assert engine is not None

    sm = get_sessionmaker()
    assert sm is not None

    async for session in get_db_session():
        assert session is not None
        break

    await close_engine()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unit_of_work_lifecycle():
    mock_session = AsyncMock()
    uow = UnitOfWork(session=mock_session)

    async with uow as active_uow:
        assert active_uow.session == mock_session
        await active_uow.commit()
        mock_session.commit.assert_called()

        await active_uow.rollback()
        mock_session.rollback.assert_called()

    # Automatic commit on clean exit
    mock_session.commit.assert_called()
