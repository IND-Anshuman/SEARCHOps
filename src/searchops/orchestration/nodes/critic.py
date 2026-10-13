"""
critic_node: LLM-driven reflection that grades evidence against the query.

Replaces the original ``evaluator_node`` stub (which only incremented an
iteration counter). The critic examines the accumulated ``Claim`` set from the
user's perspective and emits typed ``EvidenceGap`` objects — each carrying an
optional follow-up ``target_query``. This is what lets the loop decide to
re-search / re-scrape instead of blindly finishing.

Token economy:
- ONE critic call per round (not per-claim) — the LLM pattern-matches across
  the whole claim set in a single pass, so overhead is bounded.
- temperature=0.0 and a strict JSON schema → deterministic, cacheable output.
- Anti-sycophancy: the prompt forbids deferring to prior rounds.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from searchops.core.observability.metrics import NODE_INVOCATIONS_TOTAL
from searchops.orchestration.evidence import Claim, EvidenceGap

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

_SYSTEM_CRITIC_PROMPT = """You are a rigorous research critic. Your job is to judge whether the
supplied EVIDENCE CLAIMS adequately answer the user's QUERY.

Return ONLY valid JSON matching this schema:
{
  "gaps": [
    {"description": "what information is missing/weak",
     "target_query": "a specific search query that would close this gap",
     "priority": 1}
  ],
  "classifications": {"<claim_id>": "supported" | "contested" | "unverified"}
}

Rules:
- Identify genuine gaps in coverage, not superficial ones. Prioritize 1 (highest)..5 (lowest).
- Return gaps=[] when the evidence already answers the query well.
- Do NOT defer to prior rounds' opinions — re-evaluate from scratch each call.
- Return NO markdown, NO commentary — only the JSON object."""


def _clean_json(raw: str) -> dict[str, Any]:
    """Strip code fences and parse JSON, returning a dict or {} on failure."""
    clean = raw.strip()
    if "```" in clean:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)
        if m:
            clean = m.group(1).strip()
    try:
        parsed = orjson.loads(clean)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def critic_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Grade evidence vs. the query and emit typed coverage gaps."""
    query = state.get("query", "")
    claims: list[Claim] = state.get("evidence_claims", [])
    log.info("Critic node executing", query=query, claims=len(claims))

    # No evidence yet → emit a single top-priority gap to trigger initial search.
    if not claims:
        log.info("Critic: no evidence yet, emitting initial gap")
        NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="skipped").inc()
        return {
            "evidence_gaps": [
                EvidenceGap(
                    description="No evidence has been gathered yet; perform an initial web search.",
                    target_query=query,
                    priority=1,
                )
            ]
        }

    NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="invoked").inc()

    claims_brief = "\n".join(f"- [{c.id}] ({c.status}) {c.text[:160]}" for c in claims[:20]) or "None"

    raw = await llm_router.generate(
        prompt=f"QUERY: {query}\n\nEVIDENCE CLAIMS:\n{claims_brief}",
        system_prompt=_SYSTEM_CRITIC_PROMPT,
        temperature=0.0,
    )

    parsed = _clean_json(raw)
    gaps_parsed = parsed.get("gaps", []) or []
    class_map = parsed.get("classifications", {}) or {}

    gaps: list[EvidenceGap] = []
    for g in gaps_parsed[:10]:  # max 10 gaps per round
        if not isinstance(g, dict):
            continue
        desc = (g.get("description") or "").strip()
        if not desc:
            continue
        try:
            prio = int(g.get("priority", 1))
        except (TypeError, ValueError):
            prio = 1
        gaps.append(
            EvidenceGap(
                description=desc[:300],
                target_query=(g.get("target_query") or "").strip()[:300],
                priority=prio,
            )
        )

    # Apply per-claim status classifications to the accumulated claims.
    updated_claims: list[Claim] = []
    for claim in claims:
        status = class_map.get(claim.id)
        if status in ("supported", "contested", "refuted"):
            updated_claims.append(claim.model_copy(update={"status": status}))
        else:
            updated_claims.append(claim)

    if not gaps and claims:
        # No gaps — all evidence satisfies the query.
        log.info("Critic: evidence judged sufficient, no gaps")
        NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="ok").inc()

    log.info("Critic node complete", gap_count=len(gaps))
    return {"evidence_gaps": gaps, "evidence_claims": updated_claims}
