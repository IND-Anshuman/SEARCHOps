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
import re

import orjson
import structlog

from searchops.core.observability.metrics import EXTRACT_CLAIMS_TOTAL, NODE_INVOCATIONS_TOTAL
from searchops.llm.router import LLMRouter
from searchops.llm.token_budget import MAX_DOC_CHARS
from searchops.orchestration.evidence import Citation, Claim
from searchops.orchestration.schemas.claims import ClaimsResult
from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)

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


async def _extract_from_doc(llm_router: LLMRouter, doc: dict) -> list[Claim]:
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

    try:
        clean = raw.strip()
        if "```" in clean:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)
            if m:
                clean = m.group(1).strip()
        parsed = ClaimsResult.model_validate(orjson.loads(clean))
    except Exception as exc:
        log.error("Failed to parse claim extraction", url=url, error=str(exc))
        NODE_INVOCATIONS_TOTAL.labels(node="extract_claims", outcome="error").inc()
        return []

    claims: list[Claim] = []
    for i, c in enumerate(parsed.claims):
        if not c.text.strip():
            continue
        citations = [
            Citation(
                url=cit.url,
                title=cit.title,
                date=cit.date,
                author=cit.author,
                excerpt=(cit.excerpt or "")[:280],
            )
            for cit in c.citations
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
        if isinstance(res, Exception):
            log.error("Doc claim extraction task failed", error=str(res))
            continue
        claims.extend(res)

    log.info("Extract claims node complete", claims=len(claims))
    return {"evidence_claims": claims}