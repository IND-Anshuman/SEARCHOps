"""
Neo4j Graph Database Repository Implementation with IGraphStore & Batched Cypher UNWIND Upserts.
"""

from __future__ import annotations

import structlog
from neo4j import AsyncDriver

from searchops.core.interfaces.storage import IGraphStore
from searchops.infrastructure.graph.neo4j import get_neo4j_driver
from searchops.knowledge.domain.entity import KGEntity, KGRelation

log = structlog.get_logger(__name__)


class Neo4jGraphRepository(IGraphStore):
    """Repository for persisting entities and relationships to Neo4j using canonical deduplication and IGraphStore port."""

    def __init__(self, driver: AsyncDriver | None = None) -> None:
        self.driver = driver or get_neo4j_driver()

    async def init_indexes(self) -> None:
        """Create uniqueness constraint and indexes on canonical_id."""
        queries = [
            "CREATE CONSTRAINT entity_canonical_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.canonical_id IS UNIQUE;",
            "CREATE INDEX entity_name_index IF NOT EXISTS FOR (e:Entity) ON (e.name);",
        ]
        try:
            async with self.driver.session() as session:
                for q in queries:
                    await session.run(q)
            log.info("Neo4j indexes initialized successfully")
        except Exception as exc:
            log.warning("Failed to initialize Neo4j indexes", error=str(exc))

    async def upsert_entity(self, entity: KGEntity) -> None:
        """Upsert a Knowledge Graph node entity merged on canonical_id."""
        query = """
        MERGE (e:Entity {canonical_id: $canonical_id})
        SET e.id = $id,
            e.name = $name,
            e.entity_type = $entity_type,
            e.description = $description,
            e.confidence = $confidence,
            e.updated_at = datetime()
        RETURN e
        """
        try:
            async with self.driver.session() as session:
                await session.run(
                    query,
                    canonical_id=entity.canonical_id,
                    id=entity.id,
                    name=entity.name,
                    entity_type=entity.entity_type,
                    description=entity.description,
                    confidence=entity.confidence,
                )
        except Exception as exc:
            log.error("Failed to upsert Neo4j entity", canonical_id=entity.canonical_id, error=str(exc))

    async def upsert_relation(self, relation: KGRelation) -> None:
        """Upsert a Knowledge Graph edge relationship between canonical nodes."""
        src_cid = relation.source_canonical_id or relation.source_id
        tgt_cid = relation.target_canonical_id or relation.target_id

        query = """
        MATCH (src:Entity {canonical_id: $source_canonical_id})
        MATCH (tgt:Entity {canonical_id: $target_canonical_id})
        MERGE (src)-[r:RELATION {relation_type: $relation_type}]->(tgt)
        SET r.id = $id,
            r.description = $description,
            r.weight = $weight,
            r.updated_at = datetime()
        """
        try:
            async with self.driver.session() as session:
                await session.run(
                    query,
                    id=relation.id,
                    source_canonical_id=src_cid,
                    target_canonical_id=tgt_cid,
                    relation_type=relation.relation_type,
                    description=relation.description,
                    weight=relation.weight,
                )
        except Exception as exc:
            log.error("Failed to upsert Neo4j relation", relation_id=relation.id, error=str(exc))

    async def upsert_entities_batch(self, entities: list[KGEntity]) -> None:
        """Perform high-performance batched UNWIND upsert of entities."""
        if not entities:
            return

        payload = [
            {
                "canonical_id": e.canonical_id,
                "id": e.id,
                "name": e.name,
                "entity_type": e.entity_type,
                "description": e.description,
                "confidence": e.confidence,
            }
            for e in entities
        ]

        query = """
        UNWIND $batch AS item
        MERGE (e:Entity {canonical_id: item.canonical_id})
        SET e.id = item.id,
            e.name = item.name,
            e.entity_type = item.entity_type,
            e.description = item.description,
            e.confidence = item.confidence,
            e.updated_at = datetime()
        """
        try:
            async with self.driver.session() as session:
                await session.run(query, batch=payload)
            log.info("Batch entities upsert complete", count=len(entities))
        except Exception as exc:
            log.error("Failed batch entities upsert", error=str(exc))

    async def upsert_relations_batch(self, relations: list[KGRelation]) -> None:
        """Perform high-performance batched Cypher UNWIND upsert of relationships."""
        if not relations:
            return

        payload = [
            {
                "id": r.id,
                "source_canonical_id": r.source_canonical_id or r.source_id,
                "target_canonical_id": r.target_canonical_id or r.target_id,
                "relation_type": r.relation_type,
                "description": r.description,
                "weight": r.weight,
            }
            for r in relations
        ]

        query = """
        UNWIND $batch AS item
        MERGE (src:Entity {canonical_id: item.source_canonical_id})
        MERGE (tgt:Entity {canonical_id: item.target_canonical_id})
        MERGE (src)-[r:RELATION {relation_type: item.relation_type}]->(tgt)
        SET r.id = item.id,
            r.description = item.description,
            r.weight = item.weight,
            r.updated_at = datetime()
        """
        try:
            async with self.driver.session() as session:
                await session.run(query, batch=payload)
            log.info("Batch relations upsert complete", count=len(relations))
        except Exception as exc:
            log.error("Failed batch relations upsert", error=str(exc))

    async def extract_subgraph(
        self,
        canonical_ids: list[str],
        max_hops: int = 2,
        limit: int = 50,
    ) -> dict[str, list[dict[str, Any]]]:
        """Perform Cypher k-hop path traversal to extract local Knowledge Graph context."""
        if not canonical_ids:
            return {"nodes": [], "edges": []}

        # Safe bounds to prevent Cypher traversal combinatorial explosion
        safe_canonical_ids = list(set(canonical_ids))[:50]
        safe_hops = max(1, min(max_hops, 3))

        query = f"""
        MATCH (src:Entity) WHERE src.canonical_id IN $canonical_ids
        MATCH path = (src)-[r:RELATION*1..{safe_hops}]-(target:Entity)
        WITH nodes(path) AS ns, relationships(path) AS rs
        UNWIND ns AS n
        UNWIND rs AS rel
        RETURN collect(DISTINCT {{
            canonical_id: n.canonical_id,
            name: n.name,
            entity_type: n.entity_type,
            description: n.description
        }})[..$limit] AS nodes,
        collect(DISTINCT {{
            source: startNode(rel).canonical_id,
            target: endNode(rel).canonical_id,
            relation_type: rel.relation_type,
            description: rel.description
        }})[..$limit] AS edges
        """
        try:
            async with self.driver.session() as session:
                result = await session.run(query, canonical_ids=safe_canonical_ids, limit=limit)
                record = await result.single()
                if record:
                    return {
                        "nodes": record.get("nodes", []) or [],
                        "edges": record.get("edges", []) or [],
                    }
        except Exception as exc:
            log.warning("GraphRAG subgraph extraction query fallback", error=str(exc))

        return {"nodes": [], "edges": []}
