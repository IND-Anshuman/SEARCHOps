"""
NewType definitions for domain value primitives.

NewTypes are checked by mypy but have zero runtime overhead.
They make function signatures self-documenting and prevent
accidental argument transposition.
"""
from __future__ import annotations

from typing import NewType

# ─── Numeric domain types ─────────────────────────────────────────────────────

#: A count of LLM tokens (must be >= 0)
TokenCount = NewType("TokenCount", int)

#: A cost expressed in US dollars (must be >= 0.0)
CostUSD = NewType("CostUSD", float)

#: A strictly positive integer
PositiveInt = NewType("PositiveInt", int)

#: A strictly positive float
PositiveFloat = NewType("PositiveFloat", float)

#: A float in [0.0, 1.0] representing a percentage or probability
PercentFloat = NewType("PercentFloat", float)

#: A confidence score in [0.0, 1.0]
ConfidenceScore = NewType("ConfidenceScore", float)

#: A similarity score in [-1.0, 1.0] or [0.0, 1.0] depending on metric
SimilarityScore = NewType("SimilarityScore", float)

#: An embedding vector dimension count
EmbeddingDimension = NewType("EmbeddingDimension", int)

#: A timestamp in seconds since epoch (Unix time)
UnixTimestamp = NewType("UnixTimestamp", float)

#: A duration in seconds
DurationSeconds = NewType("DurationSeconds", float)
