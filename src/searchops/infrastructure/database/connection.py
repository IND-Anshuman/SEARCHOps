"""
Async SQLAlchemy 2.0 database engine and session management.

Design decisions:
- Always use async driver (asyncpg for PostgreSQL, sqlite+aiosqlite for testing)
- Connection pooling configured via DatabaseSettings (pool_size, max_overflow, pool_recycle)
- NullPool used automatically during testing to prevent cross-test connection leaks
- Session factory returns AsyncSession with expire_on_commit=False (prevents DetachedInstanceError)
- Single source of truth for session management across the entire application
"""

from __future__ import annotations

from typing import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from searchops.config.settings import Settings, get_settings

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return or create the global AsyncEngine singleton.

    Args:
        settings: Optional settings override. Uses get_settings() if omitted.

    Returns:
        AsyncEngine instance.
    """
    global _engine
    if _engine is not None:
        return _engine

    cfg = settings or get_settings()
    db_cfg = cfg.database

    if cfg.env == "testing":
        # Use NullPool in testing to avoid state leakage between test functions
        _engine = create_async_engine(
            db_cfg.async_url,
            echo=False,
            poolclass=NullPool,
        )
    else:
        _engine = create_async_engine(
            db_cfg.async_url,
            echo=db_cfg.echo,
            echo_pool=db_cfg.echo_pool,
            pool_size=db_cfg.pool_size,
            max_overflow=db_cfg.max_overflow,
            pool_timeout=db_cfg.pool_timeout,
            pool_recycle=db_cfg.pool_recycle,
            pool_pre_ping=db_cfg.pool_pre_ping,
        )

    log.info(
        "Database engine created",
        host=db_cfg.host,
        port=db_cfg.port,
        database=db_cfg.name,
        pool_size=db_cfg.pool_size,
    )
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Return or create the global async_sessionmaker singleton.

    Returns:
        async_sessionmaker for creating AsyncSession instances.
    """
    global _sessionmaker
    if _sessionmaker is not None:
        return _sessionmaker

    engine = get_engine(settings)
    _sessionmaker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    return _sessionmaker


async def get_db_session(
    settings: Settings | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for yielding request-scoped database sessions.

    Yields:
        AsyncSession instance that automatically closes after request completion.
    """
    factory = get_sessionmaker(settings)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    """Close the global database engine and dispose of the pool.

    Called during application shutdown.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        log.info("Database engine disposed")
        _engine = None
        _sessionmaker = None
