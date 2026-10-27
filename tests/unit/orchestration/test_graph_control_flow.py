"""
Unit tests for the rewired deep-research graph control flow:

  1. should_continue 3-arm routing (gap_analyst / state_pruner / loop cap)
  2. gap_analyst_node pure-function (gap → sub-query, de-dup, loop_count++)
"""

from __future__ import annotations

import pytest

from searchops.core.context.research import ResearchDepth, ResearchPlan
from searchops.orchestration.evidence import Claim, EvidenceGap
from searchops.orchestration.graphs.deep_research import should_continue
from searchops.orchestration.nodes.gap_analyst import gap_analyst_node


def _claims(n=1) -> list[Claim]:
    return [Claim(id=f"c{i}", text=f"claim {i}") for i in range(n)]


@pytest.mark.unit
def test_should_continue_no_gaps_goes_to_synthesis():
    state = {
        "query": "q",
        "evidence_claims": _claims(5),
        "evidence_gaps": [],
        "loop_count": 0,
        "depth": ResearchDepth.STANDARD,
    }
    assert should_continue(state) == "state_pruner"


@pytest.mark.unit
def test_should_continue_gaps_replan():
    state = {
        "query": "q",
        "evidence_claims": _claims(1),
        "evidence_gaps": [EvidenceGap(description="missing X", target_query="X", priority=1)],
        "loop_count": 0,
        "depth": ResearchDepth.DEEP,
    }
    assert should_continue(state) == "gap_analyst"


@pytest.mark.unit
def test_should_continue_loop_cap_terminates():
    state = {
        "query": "q",
        "evidence_claims": _claims(1),
        "evidence_gaps": [EvidenceGap(description="missing X", priority=1)],
        "loop_count": 4,
        "depth": ResearchDepth.DEEP,
    }  # DEEP cap is 4
    assert should_continue(state) == "state_pruner"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gap_analyst_converts_gaps_to_queries():
    plan = ResearchPlan(primary_query="BD vs ZenRows 2026", sub_queries=["BD vs ZenRows 2026"], search_budget=2)
    state = {
        "plan": plan,
        "loop_count": 0,
        "depth": ResearchDepth.DEEP,
        "evidence_gaps": [
            EvidenceGap(description="pricing", target_query="Bright Data pricing 2026", priority=1),
        ],
        "evidence_claims": [],
    }
    out = await gap_analyst_node(state)
    assert out["loop_count"] == 1
    assert "Bright Data pricing 2026" in out["plan"].sub_queries


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gap_analyst_dedupes_near_duplicates():
    plan = ResearchPlan(primary_query="q", sub_queries=["BD pricing"], search_budget=1)
    state = {
        "plan": plan,
        "loop_count": 0,
        "depth": ResearchDepth.DEEP,
        "evidence_gaps": [
            EvidenceGap(description="?", target_query="BD Pricing", priority=1),  # ~dup of "BD pricing"
        ],
        "evidence_claims": [],
    }
    out = await gap_analyst_node(state)
    # near-duplicate filtered; only the original query remains
    assert out["plan"].sub_queries == ["BD pricing"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gap_analyst_no_gaps_noop():
    state = {
        "plan": ResearchPlan(primary_query="q", sub_queries=[]),
        "loop_count": 0,
        "evidence_gaps": [],
        "evidence_claims": [],
    }
    out = await gap_analyst_node(state)
    assert out == {}
