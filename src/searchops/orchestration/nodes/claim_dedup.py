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


_FIGURE_RE = re.compile(r"\d+(?:[.,]\d+)?")
_STATUS_RANK = {"supported": 0, "unverified": 1, "contested": 2, "refuted": 3}
# A claim this short is too generic to call contained ("Milvus is fast").
_MIN_CONTAINED_WORDS = 4


def absorb_contained(claims: list[Claim]) -> list[Claim]:
    """Fold a claim into a longer one that already states all of it.

    "Milvus is licensed under Apache 2.0" adds nothing next to "Milvus and
    Qdrant are both released under Apache 2.0, which permits commercial use";
    kept apart, the report states the licence twice. A claim is absorbed only
    when every content word and every figure recurs in the other claim and the
    other claim is verified at least as strongly, so nothing is lost. The
    absorbing claim inherits the citations. Report stage only: verification
    still sees every claim.
    """
    sigs = [_signature(c.text) for c in claims]
    figs = [frozenset(_FIGURE_RE.findall(c.text)) for c in claims]
    rank = [_STATUS_RANK.get(c.status, 1) for c in claims]
    absorbed_into: dict[int, int] = {}
    for i, small in enumerate(sigs):
        if len(small) < _MIN_CONTAINED_WORDS:
            continue
        for j, big in enumerate(sigs):
            if i == j or j in absorbed_into or len(big) <= len(small):
                continue
            if small <= big and figs[i] <= figs[j] and rank[j] <= rank[i]:
                absorbed_into[i] = j
                break
    out: dict[int, Claim] = {j: c for j, c in enumerate(claims) if j not in absorbed_into}
    for i, j in absorbed_into.items():
        while j in absorbed_into:  # the absorber was itself absorbed later
            j = absorbed_into[j]
        merged = _merge(out[j], claims[i])
        urls = list(dict.fromkeys([*merged.supporting_urls, *claims[i].supporting_urls]))
        out[j] = merged.model_copy(update={"supporting_urls": urls})
    return [out[k] for k in sorted(out)]
