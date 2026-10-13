"""
LLM-Powered Entity & Relationship Extractor with Pydantic Structured Outputs & System Prompt Caching.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.knowledge.schemas.extraction import ExtractionResult
from searchops.llm.json_repair import TIER_SPAN_SALVAGE, repair_json
from searchops.llm.repair_memory import get_repair_memory

if TYPE_CHECKING:
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

        # Repair ladder — same contract as the claim extractor: recover what is
        # recoverable, count every outcome, never silently drop a document.
        outcome = repair_json(
            raw_response,
            record_key="entities",
            field_keys=("name", "description"),
        )
        memory = get_repair_memory()

        if outcome.parsed is None:
            memory.record("entity_extraction", TIER_SPAN_SALVAGE, recovered=False)
            log.error(
                "entity extraction unrecoverable",
                tier=outcome.tier_name,
                raw_head=raw_response[:200],
            )
            return [], []

        memory.record("entity_extraction", outcome.repair_tier, recovered=True)

        if outcome.salvaged:
            log.info(
                "entities partially salvaged",
                recovered=outcome.recovered_count,
                tier=outcome.tier_name,
            )

        try:
            result = ExtractionResult.model_validate(outcome.parsed)

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

            def _resolve(name: str) -> KGEntity | None:
                """Entity for *name*, materialising a referenced-only endpoint.

                The model routinely names a relation endpoint it never declares
                as an entity (``USES: Python -> CPython`` with only ``Python``
                in ``entities``). Dropping the whole relation over that silently
                lost graph structure, so a placeholder entity is created and the
                relation is kept. The placeholder is marked ``description=""``
                and ``entity_type="Concept"`` so it is distinguishable upstream
                from a fully-described entity.
                """
                key = name.strip()
                if not key:
                    return None
                existing = entity_map.get(key)
                if existing is not None:
                    return existing
                placeholder = KGEntity(name=key, entity_type="Concept", description="")
                entity_map[key] = placeholder
                return placeholder

            for rel in result.relations:
                src = _resolve(rel.source)
                tgt = _resolve(rel.target)
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
            # A salvaged payload can fail schema validation when a required
            # field never survived truncation. That is a partial outcome — it is
            # counted and logged, never presented as a clean parse.
            log.error(
                "Failed to parse extracted entities",
                tier=outcome.tier_name,
                salvaged=outcome.salvaged,
                raw_response=raw_response[:200],
                error=str(exc),
            )
            memory.record("entity_extraction", TIER_SPAN_SALVAGE, recovered=False)
            return [], []

    async def extract_batch(self, documents: list[str], batch_size: int = 4) -> tuple[list[KGEntity], list[KGRelation]]:
        """Perform concurrent batched entity & relation extraction across multiple documents (max 4 docs/call)."""
        if not documents:
            return [], []

        batches = [documents[i : i + batch_size] for i in range(0, len(documents), batch_size)]
        sem = asyncio.Semaphore(5)

        async def _extract_bounded(
            batch_docs: list[str],
        ) -> tuple[list[KGEntity], list[KGRelation]]:
            async with sem:
                return await self.extract("\n\n--- DOCUMENT SEPARATOR ---\n\n".join(batch_docs))

        tasks = [_extract_bounded(batch) for batch in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_entities: list[KGEntity] = []
        all_relations: list[KGRelation] = []
        entity_by_canonical: dict[str, KGEntity] = {}

        for res in results:
            if isinstance(res, BaseException):
                log.error("Batch entity extraction task failed", error=str(res))
                continue
            entities, relations = res
            for e in entities:
                if e.canonical_id not in entity_by_canonical:
                    entity_by_canonical[e.canonical_id] = e
                    all_entities.append(e)

            all_relations.extend(relations)

        return all_entities, all_relations
