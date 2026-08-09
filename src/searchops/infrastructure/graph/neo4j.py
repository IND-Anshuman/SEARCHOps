"""
Neo4j Graph Database Driver wrapper.
"""

from __future__ import annotations

import structlog
from neo4j import AsyncDriver, AsyncGraphDatabase

from searchops.config.settings import Settings, get_settings
from searchops.core.exceptions.infrastructure import GraphDatabaseError

log = structlog.get_logger(__name__)

_neo4j_driver: AsyncDriver | None = None


class MockNeo4jSession:
    """Mock Neo4j session for offline database-free execution."""
    async def __aenter__(self) -> MockNeo4jSession:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    async def run(self, *args, **kwargs) -> MockNeo4jResult:
        return MockNeo4jResult()


class MockNeo4jResult:
    """Mock Neo4j result iterator."""
    async def single(self) -> None:
        return None

    async def __aiter__(self) -> MockNeo4jResult:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class MockNeo4jDriver:
    """Mock Neo4j driver context manager."""
    def session(self, **kwargs) -> MockNeo4jSession:
        return MockNeo4jSession()

    async def close(self) -> None:
        pass


def get_neo4j_driver(settings: Settings | None = None) -> AsyncDriver | MockNeo4jDriver:
    """Return or initialize the global AsyncDriver singleton or a local mock."""
    global _neo4j_driver
    if _neo4j_driver is not None:
        return _neo4j_driver

    cfg = settings or get_settings()
    kg_cfg = cfg.knowledge_graph

    if kg_cfg.neo4j_uri == "mock":
        log.info("Initializing in-memory mock Neo4j driver")
        _neo4j_driver = MockNeo4jDriver()  # type: ignore
    else:
        _neo4j_driver = AsyncGraphDatabase.driver(
            kg_cfg.neo4j_uri,
            auth=(kg_cfg.neo4j_user, kg_cfg.neo4j_password.get_secret_value()),
            max_connection_pool_size=kg_cfg.neo4j_max_connection_pool_size,
        )
        log.info("Neo4j driver created", uri=kg_cfg.neo4j_uri, user=kg_cfg.neo4j_user)
    return _neo4j_driver


async def close_neo4j() -> None:
    """Close the Neo4j driver connection pool."""
    global _neo4j_driver
    if _neo4j_driver is not None:
        await _neo4j_driver.close()
        log.info("Neo4j driver closed")
        _neo4j_driver = None
