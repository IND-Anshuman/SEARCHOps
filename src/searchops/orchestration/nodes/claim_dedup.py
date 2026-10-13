"""Claim-level deduplication — the free volume cut before verification.

Why this stage exists
---------------------
At corpus scale the same fact appears on dozens of pages. Verifying each
occurrence separately pays an LLM call to reach the same verdict repeatedly.
Collapsing near-identical claims costs nothing (no LLM, no network), so it is
the cheapest saving in the funnel after the relevance filter.

Design decisions
----------------
* **Deterministic.** Same claims in, same survivors out — auditable, and
  cheap enough to run on every claim.
* **Evidence is merged, never lost.** The survivor inherits the union of its
  duplicates' citations, so collapsing text never discards a source.
* **First occurrence survives**, preserving the order claims were extracted.
* **Aggressiveness is a parameter.** Corpus mode treats looser paraphrases as
  duplicates; precision mode is conservative because dropping a subtly
  different claim loses real signal.
* **Inputs are never mutated** — merged survivors are copies.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, overload

from searchops.knowledge.evaluation.relevance import _stem, _tokens

if TYPE_CHECKING:
    from searchops.orchestration.evidence import Claim

_WORD_RE = re.compile(r"[a-z0-9]+")


def _signature(text: str) -> frozenset[str]:
    """Stemmed content-word set used to compare two claims.

    Stopwords are dropped so "the" appearing in one phrasing and not the other
    cannot separate two otherwise identical facts.
    """
    return frozenset(_stem(t) for t in _tokens(text))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _merge(primary: Claim, duplicate: Claim) -> Claim:
    """Return *primary* with *duplicate*'s citations folded in, deduped."""
    seen: set[tuple[str, str]] = {(c.url, c.excerpt) for c in primary.citations}
    merged = list(primary.citations)
    for citation in duplicate.citations:
        key = (citation.url, citation.excerpt)
        if key not in seen:
            seen.add(key)
            merged.append(citation)
    return primary.model_copy(update={"citations": merged})


@overload
def dedupe_semantically(
    claims: list[Claim],
    *,
    aggressive: bool = ...,
    return_merged: Literal[False] = ...,
) -> list[Claim]: ...


@overload
def dedupe_semantically(
    claims: list[Claim],
    *,
    aggressive: bool = ...,
    return_merged: Literal[True],
) -> tuple[list[Claim], list[str]]: ...


def dedupe_semantically(
    claims: list[Claim],
    *,
    aggressive: bool = False,
    return_merged: bool = False,
) -> list[Claim] | tuple[list[Claim], list[str]]:
    """Collapse near-identical claims, merging their citations.

    Args:
        claims: Claims in extraction order.
        aggressive: Use a looser similarity bar (corpus mode).
        return_merged: Also return the ids that were absorbed.

    Returns:
        The surviving claims, or ``(survivors, merged_ids)`` when
        *return_merged* is set.
    """
    threshold = 0.8 if aggressive else 0.9
    survivors: list[Claim] = []
    signatures: list[frozenset[str]] = []
    merged_ids: list[str] = []

    for claim in claims:
        sig = _signature(claim.text)
        placed = False
        for i, existing in enumerate(signatures):
            if _jaccard(sig, existing) >= threshold:
                survivors[i] = _merge(survivors[i], claim)
                merged_ids.append(claim.id)
                placed = True
                break
        if not placed:
            survivors.append(claim.model_copy())
            signatures.append(sig)

    if return_merged:
        return survivors, merged_ids
    return survivors
