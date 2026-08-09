"""
Unit of Work (UoW) Pattern implementation for atomic database transactions.

Ensures that multiple repository operations within a single business transaction
either all succeed (commit) or all fail (rollback), maintaining ACID guarantees.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from searchops.core.exceptions.infrastructure import DatabaseError
from searchops.infrastructure.database.connection import get_sessionmaker

log = structlog.get_logger(__name__)


class UnitOfWork:
    """Async Unit of Work for managing database transaction boundaries.

    Usage:
        async with UnitOfWork() as uow:
            await uow.session.execute(...)
            # automatic commit on exit if no exception raised
            # automatic rollback on exception
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._external_session = session is not None
        self._session = session
        self._session_factory = get_sessionmaker() if session is None else None

    @property
    def session(self) -> AsyncSession:
        """Return the current active session."""
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active. Use 'async with UnitOfWork():'.")
        return self._session

    async def __aenter__(self) -> Self:
        """Enter transaction context."""
        if self._session is None and self._session_factory is not None:
            self._session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit transaction context with automatic commit/rollback."""
        if self._session is None:
            return

        try:
            if exc_type is not None:
                log.warning("Transaction rollback triggered by exception", error=str(exc_val))
                await self._session.rollback()
            else:
                await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            log.error("Failed to commit transaction", error=str(exc))
            raise DatabaseError("Transaction commit failed", cause=exc) from exc
        finally:
            if not self._external_session:
                await self._session.close()
                self._session = None

    async def commit(self) -> None:
        """Manually commit the active transaction."""
        if self._session is not None:
            try:
                await self._session.commit()
            except Exception as exc:
                await self._session.rollback()
                raise DatabaseError("Manual commit failed", cause=exc) from exc

    async def rollback(self) -> None:
        """Manually rollback the active transaction."""
        if self._session is not None:
            await self._session.rollback()
