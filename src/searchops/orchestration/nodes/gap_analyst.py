"""
gap_analyst_node: turn accumulated evidence gaps into follow-up sub-queries.

A PURE function — no LLM call. It reads ``evidence_gaps`` (emitted by the
critic), converts each gap into a concrete search sub-query, merges those into
the existing plan (de-duplicated against prior sub-queries so the planner does
not re-issue the same query), and bumps the loop counter. The planner then
re-runs on the next transition, but now armed with both the original query
*and* the gaps it needs to close.

This is deliberately cheap (string ops only) so the control-layer overhead of
a re-plan cycle is sub-millisecond, keeping almost all token budget inside
the actual LLM nodes (search / extract_claims / critic).
"""

from __future__ import annotations

import hashlib

import structlog

from searchops.core.context.research import ResearchPlan
from searchops.orchestration.evidence import EvidenceGap
from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

_MAX_NEW_QUERIES = 10  # do not grow the sub-query list without bound


def _jaccard_dup(a: str, b: str) -> bool:
    """Cheap near-duplicate check on lowercased whitespace-token bigrams.

    Avoids a re-search through the LLM (and the tokens that would cost).
    Two queries sharing > 80% of their bigrams are treated as duplicates.
    """
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta and not tb:
        return True
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union > 0.8 if union else False


async def gap_analyst_node(state: ResearchState) -> ResearchState:
    """Convert evidence gaps into a de-duplicated follow-up search plan."""
    gaps: list[EvidenceGap] = state.get("evidence_gaps", [])
    plan: ResearchPlan | None = state.get("plan")

    if not gaps:
        log.info("Gap analyst: no gaps, nothing to expand")
        return {}

    prior_queries = list((plan.sub_queries if plan else []))
    new_queries: list[str] = []
    for gap in sorted(gaps, key=lambda g: g.priority, reverse=True)[:_MAX_NEW_QUERIES]:
        q = gap.target_query or gap.description
        if q and not any(_jaccard_dup(q, existing) for existing in prior_queries + new_queries):
            new_queries.append(q)

    if not new_queries:
        log.info("Gap analyst: all gaps de-duplicated against prior plan")
        # Still clear gaps so the critic→gap_analyst cycle cannot re-fire the
        # same gaps next round, and return the existing plan unchanged so the
        # loop state is always consistent (callers expect a 'plan' key).
        return {
            "plan": plan,
            "loop_count": state.get("loop_count", 0) + 1,
            "evidence_gaps": [],
        }

    merged = prior_queries + new_queries
    log.info("Gap analyst expanded plan", new_queries=len(new_queries), total=len(merged))

    new_plan = ResearchPlan(
        primary_query=plan.primary_query if plan else state.get("query", ""),
        sub_queries=merged,
        search_budget=max(5, len(merged)),
        confidence=(plan.confidence if plan else 0.5),
    )
    return {
        "plan": new_plan,
        "loop_count": state.get("loop_count", 0) + 1,
        # Clear gaps so the verifier→critic→gap_analyst cycle doesn't re-fire them.
        "evidence_gaps": [],
    }
