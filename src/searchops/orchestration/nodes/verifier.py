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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from searchops.core.observability.metrics import NODE_INVOCATIONS_TOTAL, NODE_LLM_TOKENS_IN_TOTAL
from searchops.llm.token_budget import truncate_to_tokens

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
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

# A claim is "high-stakes" when its confidence sits in either tail: very high
# (it will dominate the report if wrong) or very low (it may simply be wrong).
_HIGH_STAKE_CONFIDENCE = 0.9
_LOW_STAKE_CONFIDENCE = 0.1


@dataclass(frozen=True)
class SamplingPlan:
    """How much of the claim set to verify.

    ``rate`` is the fraction sampled by striding; high-stakes claims are
    always verified regardless of rate. A rate of 1.0 means exhaustive
    (precision mode).
    """

    rate: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.rate <= 1.0:
            raise ValueError(f"sampling rate must be in [0, 1], got {self.rate}")

    @property
    def is_exhaustive(self) -> bool:
        return self.rate >= 1.0

    def describe(self) -> str:
        """Honest, user-facing description of what was actually verified."""
        if self.is_exhaustive:
            return "All claims verified."
        pct = round(self.rate * 100)
        return (
            f"Sampled verification: roughly {pct}% of claims were checked, plus every "
            "high-stakes claim. The rest remain unverified."
        )


def plan_from_scrape_plan(plan: dict[str, Any] | None) -> SamplingPlan:
    """Build the sampling plan from the job's stored ``ScrapePlan``."""
    if not plan:
        return SamplingPlan(rate=1.0)
    try:
        return SamplingPlan(rate=float(plan.get("verify_sampling_rate", 1.0)))
    except (TypeError, ValueError):
        return SamplingPlan(rate=1.0)


def _is_high_stake(claim: Claim) -> bool:
    return claim.confidence >= _HIGH_STAKE_CONFIDENCE or claim.confidence <= _LOW_STAKE_CONFIDENCE


def select_claims_to_verify(claims: list[Claim], plan: SamplingPlan) -> list[Claim]:
    """Choose which unverified claims to verify under *plan*.

    Every high-stakes claim is included regardless of rate. The remainder is
    sampled by **striding** (every Nth) rather than taking a prefix, so the
    sample spans the whole list instead of its head — a prefix sample would be
    biased toward however the extractor happened to order its output.

    Deterministic: the same claims and rate always select the same subset.
    """
    if not claims:
        return []

    candidates = [c for c in claims if c.status == "unverified"]
    if not candidates:
        return []

    if plan.is_exhaustive:
        return candidates

    selected_ids: set[str] = {c.id for c in candidates if _is_high_stake(c)}

    # At rate 0 only high-stakes claims are verified; no striding at all.
    if plan.rate <= 0.0:
        return [c for c in candidates if c.id in selected_ids]

    stride = max(1, round(1.0 / plan.rate))
    for i in range(0, len(candidates), stride):
        selected_ids.add(candidates[i].id)

    return [c for c in candidates if c.id in selected_ids]


_MAX_TOKENS = 3_000


def _clean_json(raw: str) -> dict[str, Any]:
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
    cites = (
        "\n".join(f"  - url: {c.url}\n    excerpt: {(c.excerpt or '')[:200]}" for c in claim.citations)
        or "  - (no citations)"
    )
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
    """Verify unverified claims in batches and update their status.

    Verification is **sampled** in corpus mode: every high-stakes claim plus a
    deterministic strided sample, per the job's ``verify_sampling_rate``. The
    remainder stay ``unverified`` and the counts are reported so the caller can
    never present a sample as fully verified.
    """
    claims: list[Claim] = state.get("evidence_claims", [])
    plan = plan_from_scrape_plan(state.get("scrape_plan"))
    to_verify = select_claims_to_verify(claims, plan)

    if not to_verify:
        log.info("Verifier: nothing to verify", total_claims=len(claims))
        NODE_INVOCATIONS_TOTAL.labels(node="verifier", outcome="skipped").inc()
        unverified_left = len([c for c in claims if c.status == "unverified"])
        return {
            "verification_stats": {
                "total_claims": len(claims),
                "verified": 0,
                "unverified": unverified_left,
                "sampled_out": unverified_left,
                "sampling_rate": plan.rate,
                "note": plan.describe(),
            }
        }

    log.info("Verifier: verifying selected claims", count=len(to_verify))
    verdicts: dict[str, str] = {}
    for i in range(0, len(to_verify), _BATCH_SIZE):
        batch = to_verify[i : i + _BATCH_SIZE]
        verdicts.update(await _verify_batch(llm_router, batch))

    updated: list[Claim] = []
    verified_count = 0
    for c in claims:
        if c.id in verdicts and c.status == "unverified":
            verified_count += 1
            updated.append(c.model_copy(update={"status": verdicts[c.id]}))
        else:
            updated.append(c)

    unverified_remaining = len([c for c in updated if c.status == "unverified"])
    log.info(
        "Verifier complete",
        verified=verified_count,
        unverified_remaining=unverified_remaining,
        sampling_rate=plan.rate,
    )
    return {
        "evidence_claims": updated,
        "verification_stats": {
            "total_claims": len(updated),
            "verified": verified_count,
            "unverified": unverified_remaining,
            "sampled_out": unverified_remaining,
            "sampling_rate": plan.rate,
            "note": plan.describe(),
        },
    }
