"""
Unit tests for HierarchicalCommunityDetector.
"""

from __future__ import annotations

import pytest
from searchops.knowledge.community import HierarchicalCommunityDetector
from searchops.knowledge.domain.entity import KGEntity, KGRelation


@pytest.mark.unit
def test_louvain_community_detection():
    detector = HierarchicalCommunityDetector()

    e1 = KGEntity(name="OpenAI", entity_type="Organization", canonical_id="org:openai")
    e2 = KGEntity(name="GPT-4", entity_type="Technology", canonical_id="tech:gpt4")
    e3 = KGEntity(name="Google", entity_type="Organization", canonical_id="org:google")
    e4 = KGEntity(name="Gemini", entity_type="Technology", canonical_id="tech:gemini")

    r1 = KGRelation(source_id=e1.id, target_id=e2.id, source_canonical_id="org:openai", target_canonical_id="tech:gpt4", relation_type="CREATED")
    r2 = KGRelation(source_id=e3.id, target_id=e4.id, source_canonical_id="org:google", target_canonical_id="tech:gemini", relation_type="CREATED")

    G = detector.build_network_graph([e1, e2, e3, e4], [r1, r2])
    clusters = detector.detect_communities(G)

    assert len(clusters) == 2
    assert sum(len(c.members) for c in clusters) == 4
