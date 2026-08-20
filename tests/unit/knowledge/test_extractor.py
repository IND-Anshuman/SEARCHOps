"""
Unit tests for LLM EntityExtractor.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from searchops.knowledge.extractor import EntityExtractor


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entity_extractor_empty_text():
    mock_router = AsyncMock()
    extractor = EntityExtractor(llm_router=mock_router)
    entities, relations = await extractor.extract("")
    assert entities == []
    assert relations == []
    mock_router.generate.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entity_extractor_valid_json():
    json_payload = """```json
    {
      "entities": [
        {"name": "Python", "type": "Technology", "description": "Programming language"},
        {"name": "FastAPI", "type": "Technology", "description": "Web framework"}
      ],
      "relations": [
        {"source": "FastAPI", "target": "Python", "type": "USES", "description": "built with"}
      ]
    }
    ```"""
    mock_router = AsyncMock()
    mock_router.generate.return_value = json_payload

    extractor = EntityExtractor(llm_router=mock_router)
    entities, relations = await extractor.extract("FastAPI is a Python web framework.")

    assert len(entities) == 2
    assert len(relations) == 1
    assert entities[0].name == "Python"
    assert entities[1].name == "FastAPI"
    assert relations[0].relation_type == "USES"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entity_extractor_batch():
    json_payload = '{"entities": [{"name": "A", "type": "T", "description": ""}], "relations": []}'
    mock_router = AsyncMock()
    mock_router.generate.return_value = json_payload

    extractor = EntityExtractor(llm_router=mock_router)
    entities, relations = await extractor.extract_batch(["doc1", "doc2"], batch_size=1)

    assert len(entities) == 1
    assert entities[0].name == "A"
