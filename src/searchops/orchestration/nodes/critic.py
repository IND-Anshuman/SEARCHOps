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
from pydantic import BaseModel, Field

from searchops.core.observability.metrics import NODE_INVOCATIONS_TOTAL
from searchops.orchestration.evidence import Claim, EvidenceGap

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

# Below this many claims the evidence is too thin to be "sufficient" regardless
# of what the model says; above it, a CONFIRMED sentinel is honoured and the
# reflection loop exits instead of paying for another full search→scrape→extract
# cycle to chase pedantic, low-value gaps.
_SUFFICIENCY_MIN_CLAIMS = 8

_SYSTEM_CRITIC_PROMPT = """You are a rigorous research critic. Your job is to judge whether the
supplied EVIDENCE CLAIMS adequately answer the user's QUERY.

Return ONLY valid JSON matching this schema:
{
  "status": "CONFIRMED | OPEN",
  "gaps": [
    {"description": "what information is missing/weak",
     "target_query": "a specific web search query (under 12 words) that would close this gap",
     "priority": 1}
  ]
}

SUFFICIENCY CRITERION:
If the gathered claims provide a factual answer to the core QUERY (even if minor
details or sub-topics remain), you MUST return {"status": "CONFIRMED", "gaps": []}.
As a rule of thumb, once there are at least 8 well-sourced claims that cover the
core question, treat the evidence as sufficient. Do NOT demand perfection or
exhaustive sub-topic coverage — only flag a gap that leaves the core question
genuinely unanswered or resting on a single source.

Rules:
- Set "status" to "CONFIRMED" and "gaps" to [] the moment the sufficiency criterion is met.
- Otherwise set "status" to "OPEN" and judge COVERAGE only: which parts of the query the claims
  leave unanswered or rest on a single source. Claim truth is checked separately against the
  sources; do not grade it here.
- Identify genuine gaps, not superficial ones. Priority 1 = most important .. 5 = least.
- At most 4 gaps. Each target_query must differ from the queries already searched.
- Do NOT defer to prior rounds' opinions — re-evaluate from scratch each call.
- Return NO markdown, NO commentary — only the JSON object."""


class _CriticGap(BaseModel):
    """One coverage gap in the critic's structured response."""

    description: str = ""
    target_query: str = ""
    priority: int = 1


class CriticResult(BaseModel):
    """Structured critic output: a sufficiency verdict plus any coverage gaps."""

    status: str = Field(default="OPEN", description="CONFIRMED when the evidence answers the query")
    gaps: list[_CriticGap] = Field(default_factory=list)


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

    claims_brief = (
        "\n".join(f"- {c.text[:200]} ({len({ct.url for ct in c.citations})} source)" for c in claims[:60]) or "None"
    )
    searched = "\n".join(f"- {q}" for q in (state.get("searched_queries") or [])[:30]) or "None"
    prompt = f"QUERY: {query}\n\nQUERIES ALREADY SEARCHED:\n{searched}\n\nEVIDENCE CLAIMS:\n{claims_brief}"

    # Prefer native structured output (zero JSON-repair overhead). Routers that
    # do not support it — including the test stubs — fall back to free-form
    # generation plus lenient parsing, so behaviour is unchanged for them.
    status = ""
    gaps_parsed: list[Any] = []
    structured = None
    # Coverage grading is short-reasoning work: run it on the fast model.
    fast = llm_router.fast_model() if hasattr(llm_router, "fast_model") else None
    if hasattr(llm_router, "generate_structured"):
        try:
            structured = await llm_router.generate_structured(
                prompt=prompt,
                schema=CriticResult,
                system_prompt=_SYSTEM_CRITIC_PROMPT,
                temperature=0.0,
                model=fast,
            )
        except Exception as exc:  # pragma: no cover - defensive; cascade to text path
            log.debug("Critic structured output failed; falling back to text", error=str(exc))
            structured = None

    if isinstance(structured, CriticResult):
        status = structured.status.strip().upper()
        gaps_parsed = [g.model_dump() for g in structured.gaps]
    else:
        raw = await llm_router.generate(
            prompt=prompt,
            system_prompt=_SYSTEM_CRITIC_PROMPT,
            temperature=0.0,
            model=fast,
        )
        parsed = _clean_json(raw)
        status = str(parsed.get("status", "")).strip().upper()
        gaps_parsed = parsed.get("gaps", []) or []

    # CONFIRMED sufficiency sentinel: once the model declares the evidence
    # sufficient AND there is a real body of claims to back that up, close the
    # loop now rather than re-running the whole pipeline for a second cycle.
    if status == "CONFIRMED" and len(claims) >= _SUFFICIENCY_MIN_CLAIMS:
        log.info("Critic: CONFIRMED sufficiency sentinel; closing loop", claims=len(claims))
        NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="confirmed").inc()
        return {"evidence_gaps": []}

    gaps: list[EvidenceGap] = []
    for g in gaps_parsed[:4]:  # max 4 gaps per round
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

    if not gaps and claims:
        # No gaps — all evidence satisfies the query.
        log.info("Critic: evidence judged sufficient, no gaps")
        NODE_INVOCATIONS_TOTAL.labels(node="critic", outcome="ok").inc()

    log.info("Critic node complete", gap_count=len(gaps))
    return {"evidence_gaps": gaps}
