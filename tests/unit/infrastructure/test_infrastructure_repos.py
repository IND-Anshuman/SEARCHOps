"""
Unit tests for QdrantVectorRepository, RedisCache, Neo4jGraphRepository, and Database connection logic.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from searchops.infrastructure.cache.redis import RedisCache
from searchops.infrastructure.vector.qdrant import QdrantVectorRepository
from searchops.knowledge.repository import Neo4jGraphRepository


@pytest.mark.unit
def test_qdrant_chunk_document():
    url = "https://example.com/doc"
    text = "Paragraph 1 content.\n\nParagraph 2 content.\n\nParagraph 3 content."
    chunks = QdrantVectorRepository.chunk_document(url=url, text=text, chunk_tokens=50)

    assert len(chunks) > 0
    assert chunks[0]["url"] == url
    assert "chunk_id" in chunks[0]
    assert "content" in chunks[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_qdrant_vector_repository_upsert_and_search():
    mock_client = AsyncMock()
    mock_client.get_collections.return_value = MagicMock(collections=[])
    mock_client.search.return_value = [
        MagicMock(score=0.95, payload={"url": "https://example.com", "text": "chunk text"})
    ]

    repo = QdrantVectorRepository(client=mock_client)
    await repo.init_collection("test_col", vector_size=1536)
    mock_client.create_collection.assert_called_once()

    await repo.upsert_chunks("test_col", points=[MagicMock()])
    mock_client.upsert.assert_called_once()

    results = await repo.search_similar("test_col", [0.1] * 1536, limit=5)
    assert len(results) == 1
    assert results[0]["score"] == 0.95


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redis_cache_operations():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = b'{"status": "ok"}'
    mock_redis.exists.return_value = 1
    mock_redis.delete.return_value = 1

    cache = RedisCache(client=mock_redis)

    val = await cache.get("test_key")
    assert val == {"status": "ok"}

    assert await cache.exists("test_key") is True
    assert await cache.set("test_key", {"a": 1}, ttl_seconds=60) is True
    assert await cache.delete("test_key") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_neo4j_graph_repository():
    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session

    repo = Neo4jGraphRepository(driver=mock_driver)
    await repo.init_indexes()
    assert mock_session.run.call_count == 2
