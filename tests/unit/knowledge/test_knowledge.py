"""
Unit tests for Knowledge Graph domain models and Entity Extractor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.knowledge.extractor import EntityExtractor
from searchops.llm.router import LLMRouter


@pytest.mark.unit
def test_kg_entity_and_relation_models():
    entity1 = KGEntity(name="FastAPI", entity_type="Technology", description="Web framework")
    entity2 = KGEntity(name="Python", entity_type="Language", description="Programming language")

    relation = KGRelation(
        source_id=entity1.id,
        target_id=entity2.id,
        relation_type="WRITTEN_IN",
        description="FastAPI is written in Python",
    )

    assert entity1.name == "FastAPI"
    assert relation.source_id == entity1.id
    assert relation.target_id == entity2.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_entity_extractor():
    mock_llm = AsyncMock(spec=LLMRouter)
    mock_llm.generate.return_value = """
    {
      "entities": [
        {"name": "LangGraph", "type": "Technology", "description": "Agent Orchestration"},
        {"name": "Python", "type": "Language", "description": "Language"}
      ],
      "relations": [
        {"source": "LangGraph", "target": "Python", "type": "WRITTEN_IN", "description": "Built on Python"}
      ]
    }
    """

    extractor = EntityExtractor(mock_llm)
    entities, relations = await extractor.extract("LangGraph is a Python framework.")

    assert len(entities) == 2
    assert len(relations) == 1
    assert entities[0].name == "LangGraph"
    assert relations[0].relation_type == "WRITTEN_IN"
