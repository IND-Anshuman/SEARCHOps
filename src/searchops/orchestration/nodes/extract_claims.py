"""
extract_claims_node: convert scraped documents into typed ``Claim`` objects.

Mirrors the concurrency/parse pattern of ``EntityExtractor``
(``searchops/knowledge/extractor.py``) but emits evidence ``Claim`` models
that carry their own inline ``Citation`` metadata. This is the highest-token
LLM consumer in the deep-research design, so:

- One LLM call per document (not per sentence) via a strict JSON schema.
- Each doc capped at ``MAX_DOC_CHARS`` before the call.
- Deterministic, cacheable output (temperature=0.0).
- Prometheus counters for extracted / cached / error outcomes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from searchops.core.observability.metrics import EXTRACT_CLAIMS_TOTAL, NODE_INVOCATIONS_TOTAL
from searchops.llm.json_repair import TIER_EXACT, TIER_SPAN_SALVAGE, repair_json
from searchops.llm.repair_memory import get_repair_memory
from searchops.llm.token_budget import MAX_DOC_CHARS
from searchops.orchestration.evidence import Citation, Claim
from searchops.orchestration.nodes.claim_dedup import dedupe_semantically
from searchops.orchestration.schemas.claims import ClaimsResult

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


def _domain_of(url: str) -> str:
    """Best-effort host extraction, used only as the repair-memory key."""
    try:
        return urlparse(url).netloc or "unknown"
    except Exception:
        return "unknown"


_SYSTEM_CLAIM_PROMPT = """You are a rigorous evidence extractor. Read the supplied document and
return ONLY valid JSON matching this schema:
{
  "claims": [
    {
      "text": "one standalone factual statement",
      "confidence": 0.0,
      "citations": [
        {"url": "https://...", "title": "", "date": "", "author": "", "excerpt": "short verbatim span"}
      ]
    }
  ]
}
Rules:
- Extract only claims actually stated in the document; do not infer or add outside knowledge.
- You may ONLY cite URLs that appear in the document.
- Each "text" is a single factual statement (split multi-part sentences).
- "excerpt" must be a short verbatim quote (<=280 chars).
- Return NO markdown, NO commentary — only the JSON object."""


async def _extract_from_doc(llm_router: LLMRouter, doc: dict[str, Any]) -> list[Claim]:
    """Extract typed claims for a single scraped document."""
    url = doc.get("url", "unknown")
    content = (doc.get("content") or "").strip()[:MAX_DOC_CHARS]
    if not content:
        return []

    NODE_INVOCATIONS_TOTAL.labels(node="extract_claims", outcome="invoked").inc()

    raw = await llm_router.generate(
        prompt=f"Document URL: {url}\n\nDocument title: {doc.get('title', '')}\n\nDocument text:\n{content}",
        system_prompt=_SYSTEM_CLAIM_PROMPT,
        temperature=0.0,
    )

    # Repair ladder: the model's JSON is frequently near-miss (markdown fences,
    # trailing commas, truncated tails). Before this loop those documents were
    # silently dropped — one malformed character destroyed a document's worth
    # of evidence with no record that it happened. Now every outcome is
    # counted, and salvage recovers partial evidence where it exists.
    outcome = repair_json(raw, record_key="claims", field_keys=("text",))
    domain = _domain_of(url)
    memory = get_repair_memory()

    if outcome.parsed is None:
        # Nothing was recoverable — but it is now a *recorded* failure, not a
        # silent drop.
        memory.record(domain, TIER_SPAN_SALVAGE, recovered=False)
        EXTRACT_CLAIMS_TOTAL.labels(node="extract_claims", status="error").inc()
        NODE_INVOCATIONS_TOTAL.labels(node="extract_claims", outcome="error").inc()
        log.error(
            "claim extraction unrecoverable",
            url=url,
            tier=outcome.tier_name,
            raw_head=raw[:200],
        )
        return []

    memory.record(domain, outcome.repair_tier, recovered=True)

    try:
        parsed = ClaimsResult.model_validate(outcome.parsed)
    except Exception as exc:
        # Salvaged records can miss required fields; that is an expected
        # partial outcome, not a hard failure — count it and move on.
        log.warning(
            "salvaged claims failed schema validation",
            url=url,
            tier=outcome.tier_name,
            salvaged=outcome.salvaged,
            error=str(exc),
        )
        memory.record(domain, TIER_SPAN_SALVAGE, recovered=False)
        EXTRACT_CLAIMS_TOTAL.labels(node="extract_claims", status="error").inc()
        return []

    if outcome.salvaged:
        # Partial recovery: log it so it is never mistaken for a clean parse.
        log.info(
            "claims partially salvaged",
            url=url,
            recovered=outcome.recovered_count,
            tier=outcome.tier_name,
        )
        NODE_INVOCATIONS_TOTAL.labels(node="extract_claims", outcome="salvaged").inc()
    elif outcome.repair_tier > TIER_EXACT:
        log.info(
            "claims repaired",
            url=url,
            tier=outcome.tier_name,
        )
        NODE_INVOCATIONS_TOTAL.labels(node="extract_claims", outcome="repaired").inc()

    claims: list[Claim] = []
    for i, c in enumerate(parsed.claims):
        if not c.text.strip():
            continue
        # `citations` is null-tolerant: a claim that survived without
        # citations is still evidence, so it is kept citation-less rather
        # than dropped.
        citations = [
            Citation(
                url=cit.url,
                title=cit.title,
                date=cit.date,
                author=cit.author,
                excerpt=(cit.excerpt or "")[:280],
            )
            for cit in (c.citations or [])
            if cit.url.startswith("http")
        ]
        claims.append(
            Claim(
                id=f"cl_{abs(hash(url + str(i))) % (10**8)}",
                text=c.text.strip(),
                confidence=c.confidence,
                citations=citations,
            )
        )

    EXTRACT_CLAIMS_TOTAL.labels(node="extract_claims", status="extracted").inc(len(claims))
    NODE_INVOCATIONS_TOTAL.labels(node="extract_claims", outcome="ok").inc()
    return claims


async def extract_claims_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Concurrently extract typed evidence claims from all scraped documents."""
    scraped = state.get("scraped_contents", [])
    if not scraped:
        return {"evidence_claims": []}

    log.info("Extract claims node executing", doc_count=len(scraped))

    tasks = [_extract_from_doc(llm_router, doc) for doc in scraped]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    claims: list[Claim] = []
    for res in results:
        if isinstance(res, BaseException):
            log.error("Doc claim extraction task failed", error=str(res))
            continue
        claims.extend(res)

    # Stage 6 — claim-level dedup. Free (no LLM), and at corpus scale the same
    # fact appears on many pages, so without this verification pays repeatedly
    # to reach the same verdict. Aggressive in corpus mode, conservative in
    # precision mode where a subtly different claim is real signal.
    plan = state.get("scrape_plan") or {}
    aggressive = bool(plan.get("claim_dedup_aggressive", False))
    claims, merged = dedupe_semantically(claims, aggressive=aggressive, return_merged=True)
    if merged:
        log.info("Claim dedup collapsed near-duplicates", merged=len(merged))

    log.info("Extract claims node complete", claims=len(claims))
    return {"evidence_claims": claims}
