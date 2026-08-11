"""
LLM-Powered Entity & Relationship Extractor with Pydantic Structured Outputs & System Prompt Caching.
"""

from __future__ import annotations

import asyncio
import re
import orjson
import structlog

from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.knowledge.schemas.extraction import ExtractionResult
from searchops.llm.router import LLMRouter

log = structlog.get_logger(__name__)

_SYSTEM_EXTRACTION_PROMPT = """You are a domain expert knowledge graph entity and relationship extractor.
Extract all key technologies, organizations, concepts, and relationships from the provided text into strict JSON format matching the schema:

{
  "entities": [
    {"name": "...", "type": "Technology|Organization|Concept", "description": "..."}
  ],
  "relations": [
    {"source": "...", "target": "...", "type": "USES|DEPENDS_ON|CREATED_BY|RELATED_TO", "description": "..."}
  ]
}

Return ONLY valid JSON. Do NOT include markdown formatting or extra conversational commentary."""


class EntityExtractor:
    """Extracts Knowledge Graph Entities and Relations using LLM."""

    def __init__(self, llm_router: LLMRouter) -> None:
        self.llm_router = llm_router

    async def extract(self, text: str) -> tuple[list[KGEntity], list[KGRelation]]:
        """Perform entity and relationship extraction using system prompt caching & structured validation."""
        if not text.strip():
            return [], []

        raw_response = await self.llm_router.generate(
            prompt=f"Text to extract:\n{text}",
            system_prompt=_SYSTEM_EXTRACTION_PROMPT,
            temperature=0.0,
        )

        try:
            clean_json = raw_response.strip()
            # Handle markdown code blocks robustly anywhere in text
            if "```" in clean_json:
                match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_json)
                if match:
                    clean_json = match.group(1).strip()

            parsed_raw = orjson.loads(clean_json)
            result = ExtractionResult.model_validate(parsed_raw)

            entity_map: dict[str, KGEntity] = {}
            for item in result.entities:
                if item.name.strip():
                    e = KGEntity(
                        name=item.name.strip(),
                        entity_type=item.type or "Concept",
                        description=item.description or "",
                    )
                    entity_map[item.name.strip()] = e

            relations: list[KGRelation] = []
            for rel in result.relations:
                src = entity_map.get(rel.source.strip())
                tgt = entity_map.get(rel.target.strip())
                if src and tgt:
                    r = KGRelation(
                        source_id=src.id,
                        target_id=tgt.id,
                        source_canonical_id=src.canonical_id,
                        target_canonical_id=tgt.canonical_id,
                        relation_type=rel.type or "RELATED_TO",
                        description=rel.description or "",
                    )
                    relations.append(r)

            return list(entity_map.values()), relations

        except Exception as exc:
            log.error("Failed to parse extracted entities", raw_response=raw_response[:200], error=str(exc))
            return [], []

    async def extract_batch(self, documents: list[str], batch_size: int = 4) -> tuple[list[KGEntity], list[KGRelation]]:
        """Perform concurrent batched entity & relation extraction across multiple documents (max 4 docs/call)."""
        if not documents:
            return [], []

        batches = [
            documents[i : i + batch_size]
            for i in range(0, len(documents), batch_size)
        ]
        sem = asyncio.Semaphore(5)

        async def _extract_bounded(batch_docs: list[str]) -> tuple[list[KGEntity], list[KGRelation]]:
            async with sem:
                return await self.extract("\n\n--- DOCUMENT SEPARATOR ---\n\n".join(batch_docs))

        tasks = [_extract_bounded(batch) for batch in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_entities: list[KGEntity] = []
        all_relations: list[KGRelation] = []
        entity_by_canonical: dict[str, KGEntity] = {}

        for res in results:
            if isinstance(res, Exception):
                log.error("Batch entity extraction task failed", error=str(res))
                continue
            entities, relations = res
            for e in entities:
                if e.canonical_id not in entity_by_canonical:
                    entity_by_canonical[e.canonical_id] = e
                    all_entities.append(e)

            all_relations.extend(relations)

        return all_entities, all_relations
