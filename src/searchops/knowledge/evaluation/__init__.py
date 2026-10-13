"""
Deterministic evaluation metrics for research output.

These metrics need no LLM judge, which is the point: an LLM grader shares its
family's blind spots with the model that produced the answer, and measured
self-enhancement bias runs 10-25%. What is implemented here is the ground truth
a judge is validated against, not a substitute for one.

Exports:

- :func:`key_fact_f1` - dual-track (literal 6-level + semantic cosine) F1.
- :func:`hallucination_rate` - claims asserting entities the validated corpus
  never mentions.
- :func:`citation_coverage` - claims carrying a resolvable citation.

Every result carries its raw numerator and denominator, so any score can be
traced back to the items that produced it instead of being a bare float.
"""

from searchops.knowledge.evaluation.metrics import (
    DEFAULT_SEMANTIC_THRESHOLD,
    CitationScore,
    FactScore,
    HallucinationScore,
    citation_coverage,
    hallucination_rate,
    key_fact_f1,
    literal_match_level,
    matches,
)

__all__ = [
    "DEFAULT_SEMANTIC_THRESHOLD",
    "CitationScore",
    "FactScore",
    "HallucinationScore",
    "citation_coverage",
    "hallucination_rate",
    "key_fact_f1",
    "literal_match_level",
    "matches",
]
