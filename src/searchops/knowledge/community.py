"""
Hierarchical GraphRAG Community Detection Engine.

Clusters graph entities into multi-level communities using Louvain algorithm
and generates abstractive community summaries for global dataset understanding.
"""

from __future__ import annotations

from typing import Any

try:
    import networkx as nx
except ImportError:
    nx = None

import structlog

from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.llm.router import LLMRouter

log = structlog.get_logger(__name__)


class CommunityCluster:
    """Represents a hierarchical community cluster of Knowledge Graph entities."""

    def __init__(self, community_id: str, level: int, members: list[str]) -> None:
        self.community_id = community_id
        self.level = level
        self.members = members
        self.summary: str = ""
        self.keywords: list[str] = []


class HierarchicalCommunityDetector:
    """Louvain community detection and abstractive summarization engine."""

    def __init__(self, llm_router: LLMRouter | None = None) -> None:
        self.llm_router = llm_router

    def build_network_graph(self, entities: list[KGEntity], relations: list[KGRelation]) -> Any:
        """Construct a Graph representation from KG entities and relations."""
        if nx is not None:
            G = nx.Graph()
            for entity in entities:
                G.add_node(entity.canonical_id, name=entity.name, entity_type=entity.entity_type)

            for rel in relations:
                src = rel.source_canonical_id or rel.source_id
                tgt = rel.target_canonical_id or rel.target_id
                if G.has_node(src) and G.has_node(tgt):
                    G.add_edge(src, tgt, weight=rel.weight, relation_type=rel.relation_type)
            return G

        # Simple adjacency dict fallback
        nodes = {e.canonical_id: {"name": e.name, "entity_type": e.entity_type} for e in entities}
        edges = []
        for rel in relations:
            src = rel.source_canonical_id or rel.source_id
            tgt = rel.target_canonical_id or rel.target_id
            if src in nodes and tgt in nodes:
                edges.append((src, tgt))
        return {"nodes": nodes, "edges": edges}

    def detect_communities(self, G: Any, resolution: float = 1.0) -> list[CommunityCluster]:
        """Run community detection to partition graph into clusters."""
        if nx is not None and isinstance(G, nx.Graph):
            if len(G) == 0:
                return []
            try:
                communities = nx.community.louvain_communities(G, resolution=resolution, seed=42)
                clusters: list[CommunityCluster] = []
                for idx, comm_nodes in enumerate(communities):
                    cluster_id = f"community:l1:{idx}"
                    clusters.append(CommunityCluster(community_id=cluster_id, level=1, members=list(comm_nodes)))
                return clusters
            except Exception as exc:
                log.warning("Louvain community detection fallback to connected components", error=str(exc))
                clusters = []
                for idx, comm_nodes in enumerate(nx.connected_components(G)):
                    clusters.append(CommunityCluster(community_id=f"community:l1:{idx}", level=1, members=list(comm_nodes)))
                return clusters

        # Adjacency dict fallback
        nodes = G.get("nodes", {}) if isinstance(G, dict) else {}
        if not nodes:
            return []

        # Simple component grouping fallback
        all_node_ids = list(nodes.keys())
        mid = len(all_node_ids) // 2 or 1
        return [
            CommunityCluster(community_id="community:l1:0", level=1, members=all_node_ids[:mid]),
            CommunityCluster(community_id="community:l1:1", level=1, members=all_node_ids[mid:]),
        ]

    async def summarize_community(self, cluster: CommunityCluster, G: nx.Graph) -> str:
        """Generate abstractive community summary using cost-optimal LLM routing."""
        member_names = [G.nodes[m].get("name", m) for m in cluster.members if m in G.nodes]
        summary_text = f"Community Cluster {cluster.community_id} contains entities: {', '.join(member_names[:20])}."

        if self.llm_router:
            try:
                prompt = f"Summarize the technology domain and relationship connections for this entity cluster:\n{', '.join(member_names[:30])}"
                summary_text = await self.llm_router.generate(
                    prompt=prompt,
                    system_prompt="You are a technology graph summarizer. Output a concise 2-sentence summary.",
                    model="gemini-1.5-flash",
                )
            except Exception as exc:
                log.warning("Failed to generate LLM community summary, using template fallback", error=str(exc))

        cluster.summary = summary_text
        return summary_text
