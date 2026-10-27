"""Unified evidence extraction node: claims + entities + relations in one LLM call.

Each page fetched this round is chunked into passages (see
``orchestration/passages.py``). The model reads the page's passages most
relevant to the question — not its first few thousand characters — and
returns claims, each naming the passage it came from and a verbatim quote.

Provenance is decided here in code, not by the model:
- the citation URL is the page's own URL (the model never chooses it);
- the quote is checked verbatim against the passage text, and a claim whose
  quote cannot be found is dropped rather than presented as sourced.

Token economy:
- ONE LLM call per document, bounded concurrency
- temperature=0.0 → deterministic, cacheable
- Repair ladder for JSON near-misses
- Claim dedup is free (no LLM)
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

from searchops.core.observability.metrics import EXTRACT_CLAIMS_TOTAL, NODE_INVOCATIONS_TOTAL
from searchops.knowledge.domain.entity import KGEntity, KGRelation
from searchops.llm.json_repair import TIER_EXACT, TIER_SPAN_SALVAGE, repair_json
from searchops.llm.repair_memory import get_repair_memory
from searchops.llm.schemas.unified_extraction import (
    UnifiedExtractionResult,
)
from searchops.orchestration.evidence import Citation, Claim
from searchops.orchestration.nodes.claim_dedup import dedupe_semantically
from searchops.orchestration.passages import Passage, PassageIndex, chunk_page, ground_quote

if TYPE_CHECKING:
    from searchops.llm.router import LLMRouter
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


_SYSTEM_UNIFIED_EXTRACTION_PROMPT = """You are a rigorous evidence extractor. You are given numbered PASSAGES
from one web page. Return ONLY valid JSON matching this schema:
{
  "claims": [
    {
      "text": "one standalone factual statement, understandable without the page",
      "passage": "P2",
      "quote": "the exact sentence from that passage that states this fact",
      "confidence": 0.0
    }
  ],
  "entities": [
    {"name": "...", "type": "Technology|Organization|Person|Concept|Product|Metric", "description": "..."}
  ],
  "relations": [
    {"source": "...", "target": "...", "type": "USES|DEPENDS_ON|CREATED_BY|COMPETES_WITH|PART_OF|RELATED_TO", "description": "..."}
  ]
}
Rules:
- Extract only facts the passages actually state that bear on the RESEARCH QUESTION. No outside knowledge.
- Prefer specific, checkable facts: numbers, dates, names, comparisons, results.
- "quote" MUST be copied character-for-character from the named passage (one or two sentences, <= 280 chars).
  Claims whose quote cannot be found in the page are discarded, so never paraphrase the quote.
- "text" may restate the fact in plain words but must not add anything the quote does not say.
- "confidence": how directly and unambiguously the quote states the claim (0.0-1.0).
- Return 3-12 claims for a relevant page; return "claims": [] for an irrelevant one.
- Return NO markdown, NO commentary — only the JSON object."""

# Evidence budget per page: the passages most relevant to the question, up to
# this many characters, presented in page order.
_DOC_EVIDENCE_CHARS = 9_000
_MAX_PASSAGES_PER_DOC = 10
_EXTRACT_CONCURRENCY = 6


def claim_id(url: str, text: str) -> str:
    """Stable claim id: same page + same statement -> same id, across runs.

    Python's ``hash()`` is salted per process, so ids built on it changed on
    every restart and collided across loops by list position.
    """
    norm = " ".join(text.lower().split())
    return "cl_" + hashlib.sha1(f"{url}|{norm}".encode(), usedforsecurity=False).hexdigest()[:12]


def select_passages(passages: list[Passage], focus: str) -> list[Passage]:
    """The page's passages most relevant to *focus*, in page order."""
    if not passages:
        return []
    ranked = [p for p, _ in PassageIndex(passages).search(focus, k=len(passages))]
    if not ranked:
        ranked = passages  # nothing matched lexically: read the page from the top
    chosen: list[Passage] = []
    used = 0
    for p in ranked:
        if len(chosen) >= _MAX_PASSAGES_PER_DOC:
            break
        if used + len(p.text) > _DOC_EVIDENCE_CHARS and chosen:
            continue
        chosen.append(p)
        used += len(p.text)
    return sorted(chosen, key=lambda p: p.index)


def _domain_of(url: str) -> str:
    """Best-effort host extraction, used only as the repair-memory key."""
    try:
        return urlparse(url).netloc or "unknown"
    except Exception:
        return "unknown"


def _ground_claim_quote(
    quote: str, label: str, labels: dict[str, Passage], chosen: list[Passage]
) -> tuple[str, Passage] | None:
    """Find the quote verbatim in the named passage, else in any passage shown."""
    named = labels.get(label)
    order = ([named] if named else []) + [p for p in chosen if p is not named]
    for p in order:
        found = ground_quote(quote, p.text)
        if found is not None:
            return found, p
    return None


async def _extract_from_doc(
    llm_router: LLMRouter,
    doc: dict[str, Any],
    passages: list[Passage],
    question: str,
    focus: str,
) -> tuple[list[Claim], list[KGEntity], list[KGRelation]]:
    """Extract claims, entities, and relations for a single scraped document.

    Returns (claims, entities, relations) — all three from one LLM call.
    """
    url = doc.get("url", "unknown")
    title = doc.get("title", "") or ""
    chosen = select_passages(passages, focus)
    if not chosen:
        return [], [], []

    NODE_INVOCATIONS_TOTAL.labels(node="extract_evidence", outcome="invoked").inc()

    labels = {f"P{i}": p for i, p in enumerate(chosen, 1)}
    body = "\n\n".join(f"[{label}]\n{p.text}" for label, p in labels.items())
    raw = await llm_router.generate(
        prompt=f"RESEARCH QUESTION: {question}\n\nPage: {title or url}\n\nPASSAGES:\n\n{body}",
        system_prompt=_SYSTEM_UNIFIED_EXTRACTION_PROMPT,
        temperature=0.0,
    )

    outcome = repair_json(raw, record_key="claims", field_keys=("text", "quote", "passage"))
    domain = _domain_of(url)
    memory = get_repair_memory()

    if outcome.parsed is None:
        memory.record(domain, TIER_SPAN_SALVAGE, recovered=False)
        EXTRACT_CLAIMS_TOTAL.labels(node="extract_evidence", status="error").inc()
        NODE_INVOCATIONS_TOTAL.labels(node="extract_evidence", outcome="error").inc()
        log.error(
            "evidence extraction unrecoverable",
            url=url,
            tier=outcome.tier_name,
            raw_head=raw[:200],
        )
        return [], [], []

    memory.record(domain, outcome.repair_tier, recovered=True)

    try:
        parsed = UnifiedExtractionResult.model_validate(outcome.parsed)
    except Exception as exc:
        # Salvaged records can miss required fields
        log.warning(
            "salvaged evidence failed schema validation",
            url=url,
            tier=outcome.tier_name,
            salvaged=outcome.salvaged,
            error=str(exc),
        )
        memory.record(domain, TIER_SPAN_SALVAGE, recovered=False)
        EXTRACT_CLAIMS_TOTAL.labels(node="extract_evidence", status="error").inc()
        return [], [], []

    if outcome.salvaged:
        NODE_INVOCATIONS_TOTAL.labels(node="extract_evidence", outcome="salvaged").inc()
    elif outcome.repair_tier > TIER_EXACT:
        NODE_INVOCATIONS_TOTAL.labels(node="extract_evidence", outcome="repaired").inc()

    # Map claims, grounding each quote in the page text.
    claims: list[Claim] = []
    dropped = 0
    for c in parsed.claims:
        text = c.text.strip()
        if not text:
            continue
        # No quote given: the statement itself may be copied from the page,
        # in which case it grounds itself. A paraphrase will not match.
        quote = (c.quote or "").strip() or next((ct.excerpt for ct in c.citations if ct.excerpt), "") or text
        grounded = _ground_claim_quote(quote, c.passage.strip().strip("[]").upper(), labels, chosen)
        if grounded is None:
            # Unverifiable quote: not a citation. Drop the claim rather than
            # present a paraphrase as something the source said.
            dropped += 1
            continue
        found, passage = grounded
        claims.append(
            Claim(
                id=claim_id(url, text),
                text=text,
                confidence=c.confidence,
                citations=[
                    Citation(
                        url=url,
                        title=title,
                        excerpt=found[:280],
                        passage_id=passage.id,
                        quote_verified=True,
                    )
                ],
            )
        )
    if dropped:
        log.info("Dropped claims with unverifiable quotes", url=url, dropped=dropped, kept=len(claims))

    # Map entities
    entity_map: dict[str, KGEntity] = {}
    for item in parsed.entities:
        if item.name.strip():
            e = KGEntity(
                name=item.name.strip(),
                entity_type=item.type or "Concept",
                description=item.description or "",
            )
            entity_map[item.name.strip()] = e

    # Map relations
    relations: list[KGRelation] = []

    def _resolve(name: str) -> KGEntity | None:
        key = name.strip()
        if not key:
            return None
        existing = entity_map.get(key)
        if existing is not None:
            return existing
        placeholder = KGEntity(name=key, entity_type="Concept", description="")
        entity_map[key] = placeholder
        return placeholder

    for rel in parsed.relations:
        src = _resolve(rel.source)
        tgt = _resolve(rel.target)
        if src and tgt:
            r = KGRelation(
                source_id=src.id,
                target_id=tgt.id,
                source_canonical_id=src.canonical_id,
                target_canonical_id=tgt.canonical_id,
                relation_type=rel.type or "RELATED_TO",
                description=rel.description or "",
            )
            relations.append(r)

    EXTRACT_CLAIMS_TOTAL.labels(node="extract_evidence", status="extracted").inc(len(claims))
    NODE_INVOCATIONS_TOTAL.labels(node="extract_evidence", outcome="ok").inc()
    return claims, list(entity_map.values()), relations


async def extract_evidence_node(state: ResearchState, *, llm_router: LLMRouter) -> ResearchState:
    """Chunk this round's pages into passages and extract grounded evidence.

    The new passages join the run's evidence store, which the verifier and
    the report writer retrieve from.
    """
    docs = state.get("round_documents") or []
    if not docs:
        return {"evidence_claims": [], "entities": [], "relations": [], "round_documents": []}

    question = str(state.get("query", ""))
    plan = state.get("plan")
    focus = " ".join([question, *(state.get("pending_queries") or []), *((plan.sub_queries if plan else []) or [])])
    log.info("Extract evidence node executing", doc_count=len(docs))

    page_passages = {
        d["url"]: chunk_page(d["url"], d.get("title", ""), d.get("content", "")) for d in docs if d.get("url")
    }
    semaphore = asyncio.Semaphore(_EXTRACT_CONCURRENCY)

    async def _run(doc: dict[str, Any]) -> tuple[list[Claim], list[KGEntity], list[KGRelation]]:
        async with semaphore:
            return await _extract_from_doc(llm_router, doc, page_passages.get(doc.get("url", ""), []), question, focus)

    results = await asyncio.gather(*(_run(d) for d in docs), return_exceptions=True)

    all_claims: list[Claim] = []
    all_entities: list[KGEntity] = []
    all_relations: list[KGRelation] = []

    for res in results:
        if isinstance(res, BaseException):
            log.error("Doc evidence extraction task failed", error=str(res))
            continue
        claims, entities, relations = res
        all_claims.extend(claims)
        all_entities.extend(entities)
        all_relations.extend(relations)

    # Claim-level dedup (free, no LLM)
    scrape_plan = state.get("scrape_plan") or {}
    aggressive = bool(scrape_plan.get("claim_dedup_aggressive", False))
    all_claims, merged = dedupe_semantically(all_claims, aggressive=aggressive, return_merged=True)
    if merged:
        log.info("Claim dedup collapsed near-duplicates", merged=len(merged))

    new_passages = [p for ps in page_passages.values() for p in ps]
    log.info(
        "Extract evidence node complete",
        claims=len(all_claims),
        entities=len(all_entities),
        relations=len(all_relations),
        passages=len(new_passages),
    )
    return {
        "evidence_claims": all_claims,
        "entities": all_entities,
        "relations": all_relations,
        "passages": new_passages,
        # Page text now lives in the passage store; drop the raw copies.
        "round_documents": [],
    }
