"""
evaluation_node: measure the report against the evidence that produced it.

This node makes the evaluation metrics a live part of the pipeline rather
than library code nobody calls. It runs after ``report_writer`` and computes,
from the run's own artefacts:

- **citation coverage** — what fraction of the claims carry a resolvable
  http(s) citation.
- **hallucination rate** — what fraction of claims assert a proper noun the
  validated corpus never mentions.
- **claim status mix** — how the claims broke down by verification status.

Every number is computed from the run's real data. Nothing here estimates,
and a run with no claims reports zeros with an explicit note rather than a
flattering score.

The metrics are written to state as ``evaluation_stats`` and persisted with
the job, so the trust surfaces can show them instead of inferring quality
from a count.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from searchops.knowledge.evaluation.metrics import (
    citation_coverage,
    hallucination_rate,
)

if TYPE_CHECKING:
    from searchops.orchestration.states.research_state import ResearchState

log = structlog.get_logger(__name__)


async def evaluation_node(state: ResearchState) -> ResearchState:
    """Compute evidence-quality metrics for the run.

    Pure: no LLM call, no network. The metrics are a measurement of what the
    run already produced.
    """
    claims = state.get("evidence_claims", []) or []
    # The corpus was captured by the state pruner before it stripped the raw
    # page text. Rebuilding it from scraped_contents here would find nothing,
    # because by this point every page's content is an empty string.
    corpus = str(state.get("evidence_corpus", "") or "")

    if not claims:
        log.info("Evaluation: no claims to evaluate")
        return {
            "evaluation_stats": {
                "total_claims": 0,
                "citation_coverage": 0.0,
                "cited_claims": 0,
                "hallucination_rate": None,
                "hallucination_measured": False,
                "hallucinated_claims": 0,
                "hallucinations_checked": 0,
                "status_mix": {},
                "note": "No claims were extracted, so no evidence metrics could be computed.",
            }
        }

    coverage = citation_coverage(claims)
    hallucination = hallucination_rate(claims, corpus)
    status_mix = _status_mix(claims)

    grounded = sum(1 for c in claims if _is_grounded(c))
    multi_source = sum(1 for c in claims if len(set(_field(c, "supporting_urls") or [])) >= 2)
    stats: dict[str, Any] = {
        "total_claims": len(claims),
        "citation_coverage": coverage.coverage,
        "cited_claims": coverage.cited,
        # Claims whose quote was found verbatim in the cited page.
        "grounded_claims": grounded,
        "grounding_rate": round(grounded / len(claims), 4),
        # Claims the verifier found support for in two or more distinct pages.
        "multi_source_supported": multi_source,
        "sources_cited": len({u for c in claims for u in _citation_urls(c)}),
        "hallucination_rate": hallucination.rate,
        "hallucinated_claims": hallucination.hallucinated,
        "hallucinations_checked": hallucination.checked,
        "offending_entities": list(hallucination.offending),
        "status_mix": status_mix,
        "corpus_chars": len(corpus),
    }

    if not corpus:
        # With no corpus there is no ground truth, so the hallucination check
        # could only report every entity as hallucinated. Say that it was not
        # measurable instead of publishing a meaningless number.
        stats["hallucination_rate"] = None
        stats["hallucination_measured"] = False
        stats["note"] = (
            "No validated corpus was available, so the hallucination rate was not "
            "measured. Citation coverage and the claim status mix are unaffected."
        )
    else:
        stats["hallucination_measured"] = True

    log.info(
        "Evaluation complete",
        claims=len(claims),
        citation_coverage=coverage.coverage,
        hallucination_rate=hallucination.rate,
        corpus_chars=len(corpus),
    )
    return {"evaluation_stats": stats}


def _field(claim: Any, name: str) -> Any:
    return claim.get(name) if isinstance(claim, dict) else getattr(claim, name, None)


def _citations(claim: Any) -> list[Any]:
    return list(_field(claim, "citations") or [])


def _citation_urls(claim: Any) -> list[str]:
    return [str(_field(c, "url") or "") for c in _citations(claim) if str(_field(c, "url") or "").startswith("http")]


def _is_grounded(claim: Any) -> bool:
    return any(bool(_field(c, "quote_verified")) for c in _citations(claim))


def _status_mix(claims: list[Any]) -> dict[str, int]:
    """Count claims by verification status, from models or dicts."""
    mix: dict[str, int] = {}
    for claim in claims:
        if isinstance(claim, dict):
            status = str(claim.get("status", "") or "unverified")
        else:
            status = str(getattr(claim, "status", "") or "unverified")
        mix[status] = mix.get(status, 0) + 1
    return mix
