"""
verifier_node: batch cross-source verification of accumulated claims.

For each batch of unverified claims, a single LLM call decides
``supported | contested | refuted`` by checking whether the claim's own
citations corroborate or contradict each other.

Token economy:
- Batched (≤8 claims per call) to amortize prompt overhead.
- Skipped entirely if no unverified claims → saves a token-slice on every
  round where the critic already classified everything.
- Strict JSON output + temperature=0.0 → deterministic, cacheable.
"""

from __future__ import annotations

import re

import orjson
import structlog

from searchops.core.observability.metrics import NODE_INVOCATIONS_TOTAL, NODE_LLM_TOKENS_IN_TOTAL
from searchops.llm.router import LLMRouter
from searchops.llm.token_budget import truncate_to_tokens
from searchops.orchestration.evidence import Claim
from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

_SYSTEM_VERIFY_PROMPT = """You are a fact-checking assistant. Given a claim and its cited evidences,
state whether the claim is supported, contested, or refuted.

Return ONLY valid JSON: {"verdicts":[{"claim_id":"...","verdict":"supported"|"contested"|"refuted","reason":"..."}]}

Rules:
- "supported": cited evidence corroborates the claim.
- "refuted": cited evidence contradicts the claim.
- "contested": cited evidence is mixed, weak, or missing.
- If a claim has no citations, verdict MUST be "contested".
- Return NO markdown, NO commentary."""

_BATCH_SIZE = 8
_MAX_TOKENS = 3_000


def _clean_json(raw: str) -> dict:
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


def _claim_block(claim: Claim) -> str:
    """Serialize one claim + its citations into a prompt block, token-bounded."""
    cites = "\n".join(
        f"  - url: {c.url}\n    excerpt: {(c.excerpt or '')[:200]}"
        for c in claim.citations
    ) or "  - (no citations)"
    return f'[id={claim.id}] "{claim.text}"\nCitations:\n{cites}'


async def _verify_batch(llm_router: LLMRouter, batch: list[Claim]) -> dict[str, str]:
    """Verify a batch of claims in ONE LLM call; return {claim_id → verdict."""
    blocks = "\n\n".join(_claim_block(c) for c in batch)
    prompt = f"Claims to verify:\n\n{blocks}"
    prompt = truncate_to_tokens(prompt, _MAX_TOKENS)

    NODE_INVOCATIONS_TOTAL.labels(node="verifier", outcome="invoked").inc()
    NODE_LLM_TOKENS_IN_TOTAL.labels(node="verifier", cache_hit="false").inc(len(prompt.split()))

    raw = await llm_router.generate(prompt=prompt, system_prompt=_SYSTEM_VERIFY_PROMPT, temperature=0.0)
    parsed = _clean_json(raw)
    verdicts: dict[str, str] = {}
    for v in parsed.get("verdicts", []) or []:
        if isinstance(v, dict) and v.get("claim_id") and v.get("verdict"):
            verdicts[v["claim_id"]] = v["verdict"]
    return verdicts


async def verifier_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Verify unverified claims in batches and update their status."""
    claims: list[Claim] = state.get("evidence_claims", [])
    unverified = [c for c in claims if c.status == "unverified"]

    if not unverified:
        log.info("Verifier: no unverified claims, skipping", total_claims=len(claims))
        NODE_INVOCATIONS_TOTAL.labels(node="verifier", outcome="skipped").inc()
        return {}  # no-op; LangGraph merges empty delta fine

    log.info("Verifier: verifying unverified claims", count=len(unverified))
    verdicts: dict[str, str] = {}
    for i in range(0, len(unverified), _BATCH_SIZE):
        batch = unverified[i : i + _BATCH_SIZE]
        verdicts.update(await _verify_batch(llm_router, batch))

    updated: list[Claim] = []
    verified_count = 0
    for c in claims:
        if c.id in verdicts and c.status == "unverified":
            verified_count += 1
            updated.append(c.model_copy(update={"status": verdicts[c.id]}))
        else:
            updated.append(c)

    log.info("Verifier complete", verified=verified_count, total_verdicts=len(verdicts))
    return {"evidence_claims": updated}