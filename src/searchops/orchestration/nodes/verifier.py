"""
verifier_node: check every claim against source text, across sources.

For each claim the verifier assembles an evidence set from the run's passage
store (``orchestration/passages.py``):

- the passage the claim was extracted from (its own citation), and
- the passages from *other* pages that best match the claim (BM25).

One LLM call per batch of claims labels each piece of evidence as
``supports`` / ``contradicts`` / ``irrelevant`` for its claim. The verdict is
then computed **in code** from those labels, never asserted by the model:

- ``refuted``    — evidence contradicts it and nothing supports it
- ``contested``  — some evidence supports it and some contradicts it, or no
                   evidence supports it at all
- ``supported``  — at least one source supports it and none contradicts it

The supporting and contradicting source URLs are recorded on the claim, so
the report can say "corroborated by 3 sources" and show which.

Token economy:
- Batched (≤6 claims per call), evidence windows capped per passage.
- Skipped entirely if no unverified claims.
- Strict JSON output + temperature=0.0 → deterministic, cacheable.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import orjson
import structlog

from searchops.core.observability.metrics import NODE_INVOCATIONS_TOTAL, NODE_LLM_TOKENS_IN_TOTAL
from searchops.orchestration.passages import Passage, PassageIndex, claim_support_overlap, passages_from_state

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.evidence import Claim
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

_SYSTEM_VERIFY_PROMPT = """You are a meticulous fact-checker. For each CLAIM you get numbered EVIDENCE
passages taken from web sources. Judge each piece of evidence against its claim:

- "supports":    the evidence states the claim, or states something that directly entails it
- "contradicts": the evidence states something incompatible with the claim (different number,
                 date, outcome, or the opposite assertion)
- "irrelevant":  the evidence does not bear on the claim either way

Judge only from the evidence text. Do not use outside knowledge. Partial overlap on topic is
"irrelevant", not "supports". A different figure for the same quantity is "contradicts".

Return ONLY valid JSON:
{"results":[{"claim_id":"...","labels":{"E1":"supports","E2":"irrelevant"},"reason":"one short sentence"}]}
Return NO markdown, NO commentary."""

_BATCH_SIZE = 6
_CONCURRENCY = 4
_OTHER_SOURCE_PASSAGES = 3  # independent-source passages retrieved per claim
_EVIDENCE_WINDOW_CHARS = 900

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
    sample spans the whole list instead of its head.

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

    if plan.rate <= 0.0:
        return [c for c in candidates if c.id in selected_ids]

    stride = max(1, round(1.0 / plan.rate))
    for i in range(0, len(candidates), stride):
        selected_ids.add(candidates[i].id)

    return [c for c in candidates if c.id in selected_ids]


# ── Evidence assembly ────────────────────────────────────────────────────────


@dataclass
class EvidenceItem:
    label: str
    url: str
    text: str
    own: bool = False  # the claim's own cited passage


@dataclass
class ClaimVerdict:
    """The computed outcome of verifying one claim."""

    status: str
    supporting_urls: list[str] = field(default_factory=list)
    contradicting_urls: list[str] = field(default_factory=list)
    note: str = ""


def _focus_window(text: str, anchor: str, size: int = _EVIDENCE_WINDOW_CHARS) -> str:
    """The *size*-char window of *text* most about *anchor* (a quote or claim)."""
    if len(text) <= size:
        return text
    if anchor:
        pos = text.lower().find(anchor.lower()[:60])
        if pos >= 0:
            start = max(0, pos - size // 3)
            return text[start : start + size]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    best_score, offset, best_off = -1.0, 0, 0
    for s in sentences:
        sc = claim_support_overlap(anchor, s)
        if sc > best_score:
            best_score, best_off = sc, offset
        offset += len(s) + 1
    start = max(0, best_off - size // 3)
    return text[start : start + size]


def gather_evidence(claim: Claim, index: PassageIndex | None, by_id: dict[str, Passage]) -> list[EvidenceItem]:
    """The claim's own passage plus the best passages from other sources."""
    items: list[EvidenceItem] = []
    own_urls = {c.url for c in claim.citations}
    seen: set[str] = set()
    for cit in claim.citations:
        p = by_id.get(cit.passage_id) if cit.passage_id else None
        if p is not None and p.id not in seen:
            seen.add(p.id)
            items.append(EvidenceItem("", p.url, _focus_window(p.text, cit.excerpt or claim.text), own=True))
        elif cit.excerpt:
            items.append(EvidenceItem("", cit.url, cit.excerpt, own=True))
    if index is not None and len(index):
        for p, _score in index.search(claim.text, k=_OTHER_SOURCE_PASSAGES, exclude_urls=own_urls, min_score=1.0):
            if p.id not in seen:
                seen.add(p.id)
                items.append(EvidenceItem("", p.url, _focus_window(p.text, claim.text)))
    for i, item in enumerate(items, 1):
        item.label = f"E{i}"
    return items


def decide(labels: dict[str, str], evidence: list[EvidenceItem], reason: str = "") -> ClaimVerdict:
    """Compute the verdict from per-evidence labels. Never trust a model verdict."""
    support = sorted({e.url for e in evidence if labels.get(e.label) == "supports"})
    contra = sorted({e.url for e in evidence if labels.get(e.label) == "contradicts"})
    if contra and not support:
        status = "refuted"
    elif contra and support:
        status = "contested"
    elif support:
        status = "supported"
    else:
        status = "contested"
        reason = reason or "No retrieved passage supported this claim."
    return ClaimVerdict(status=status, supporting_urls=support, contradicting_urls=contra, note=reason[:300])


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
        m = re.search(r"\{[\s\S]*\}", clean)
        if m:
            try:
                parsed = orjson.loads(m.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}


def _claim_block(claim: Claim, evidence: list[EvidenceItem]) -> str:
    ev = "\n".join(f"  [{e.label}] ({e.url}) {e.text}" for e in evidence) or "  (no evidence found)"
    return f'CLAIM [id={claim.id}]: "{claim.text}"\nEVIDENCE:\n{ev}'


async def _verify_batch(
    llm_router: LLMRouter,
    batch: list[Claim],
    evidence: dict[str, list[EvidenceItem]] | None = None,
) -> dict[str, ClaimVerdict]:
    """Verify a batch of claims in ONE LLM call; return {claim_id → verdict}."""
    evidence = evidence or {c.id: gather_evidence(c, None, {}) for c in batch}
    checkable = [c for c in batch if evidence.get(c.id)]
    verdicts: dict[str, ClaimVerdict] = {}
    for c in batch:
        if not evidence.get(c.id):
            verdicts[c.id] = ClaimVerdict(
                status="contested", note="No source passage was available to check this claim."
            )
    if not checkable:
        return verdicts

    prompt = "\n\n".join(_claim_block(c, evidence[c.id]) for c in checkable)
    NODE_INVOCATIONS_TOTAL.labels(node="verifier", outcome="invoked").inc()
    NODE_LLM_TOKENS_IN_TOTAL.labels(node="verifier", cache_hit="false").inc(len(prompt.split()))

    raw = await llm_router.generate(prompt=prompt, system_prompt=_SYSTEM_VERIFY_PROMPT, temperature=0.0)
    parsed = _clean_json(raw)
    for r in parsed.get("results", []) or []:
        if not isinstance(r, dict) or not r.get("claim_id"):
            continue
        cid = str(r["claim_id"])
        if cid not in evidence:
            continue
        labels = r.get("labels") or {}
        if not isinstance(labels, dict):
            continue
        clean = {str(k).strip("[] ").upper(): str(v).lower().strip() for k, v in labels.items()}
        verdicts[cid] = decide(clean, evidence[cid], str(r.get("reason") or ""))
    return verdicts


async def verifier_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Verify unverified claims against source passages and update their status.

    Verification is **sampled** in corpus mode: every high-stakes claim plus a
    deterministic strided sample, per the job's ``verify_sampling_rate``. The
    remainder stay ``unverified`` and the counts are reported so the caller can
    never present a sample as fully verified.
    """
    claims: list[Claim] = state.get("evidence_claims", [])
    plan = plan_from_scrape_plan(state.get("scrape_plan"))
    to_verify = select_claims_to_verify(claims, plan)

    def _stats(updated: list[Claim], verified: int) -> dict[str, Any]:
        mix: dict[str, int] = {}
        for c in updated:
            mix[c.status] = mix.get(c.status, 0) + 1
        unverified = mix.get("unverified", 0)
        corroborated = sum(1 for c in updated if len(set(c.supporting_urls)) >= 2)
        return {
            "total_claims": len(updated),
            "verified": verified,
            "unverified": unverified,
            "sampled_out": unverified,
            "sampling_rate": plan.rate,
            "status_mix": mix,
            "multi_source_supported": corroborated,
            "note": plan.describe(),
        }

    if not to_verify:
        log.info("Verifier: nothing to verify", total_claims=len(claims))
        NODE_INVOCATIONS_TOTAL.labels(node="verifier", outcome="skipped").inc()
        return {"verification_stats": _stats(claims, 0)}

    passages = passages_from_state(state.get("passages", []))
    index = PassageIndex(passages) if passages else None
    by_id = {p.id: p for p in passages}
    evidence = {c.id: gather_evidence(c, index, by_id) for c in to_verify}

    log.info("Verifier: verifying selected claims", count=len(to_verify), passages=len(passages))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _run(batch: list[Claim]) -> dict[str, ClaimVerdict]:
        async with semaphore:
            try:
                return await _verify_batch(llm_router, batch, {c.id: evidence[c.id] for c in batch})
            except Exception as exc:
                log.warning("Verifier batch failed; claims stay unverified", error=str(exc))
                return {}

    batches = [to_verify[i : i + _BATCH_SIZE] for i in range(0, len(to_verify), _BATCH_SIZE)]
    verdicts: dict[str, ClaimVerdict] = {}
    for result in await asyncio.gather(*(_run(b) for b in batches)):
        verdicts.update(result)

    updated: list[Claim] = []
    verified_count = 0
    for c in claims:
        v = verdicts.get(c.id)
        if v is not None and c.status == "unverified":
            verified_count += 1
            updated.append(
                c.model_copy(
                    update={
                        "status": v.status,
                        "supporting_urls": v.supporting_urls,
                        "contradicting_urls": v.contradicting_urls,
                        "verification_note": v.note,
                    }
                )
            )
        else:
            updated.append(c)

    stats = _stats(updated, verified_count)
    log.info("Verifier complete", **{k: v for k, v in stats.items() if k != "note"})
    return {"evidence_claims": updated, "verification_stats": stats}
