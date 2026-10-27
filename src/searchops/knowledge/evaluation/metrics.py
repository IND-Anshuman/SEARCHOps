from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

import structlog

log = structlog.get_logger(__name__)

# Cosine similarity at or above which a semantic match is declared. 0.75 is the
# threshold used by GraphRAG-TCM-style evaluations; below it, paraphrase and
# restatement stop being reliably distinguishable from different claims.
DEFAULT_SEMANTIC_THRESHOLD = 0.75

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "as",
        "into",
        "about",
        "over",
        "under",
    ]
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS]


def _stem(token: str) -> str:
    """Normalise inflections so 'runs', 'running' and 'ran' share a form.

    Strips doubling consonants before the suffix, otherwise 'running' -> 'runn'
    while 'runs' -> 'run' and the two never agree.
    """
    for suffix in ("ingly", "edly", "ly", "ing", "ies", "ied", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            base = token[: -len(suffix)]
            # 'running' -> 'runn' -> 'run'; 'stopped' -> 'stopp' -> 'stop'
            if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in "aeiou":
                base = base[:-1]
            return base
    return token


def _numbers(text: str) -> set[str]:
    """Extract normalised numeric literals so '4.2 billion' matches '4,200,000,000'.

    Magnitude words are folded into the number, because a report and its source
    routinely express the same quantity at different scales.
    """
    lowered = text.lower()
    scales = {
        "thousand": 1e3,
        "million": 1e6,
        "billion": 1e9,
        "trillion": 1e12,
    }

    out: set[str] = set()
    # A number optionally followed by a magnitude word: "4.2 billion".
    for value_str, magnitude in re.findall(r"(\d[\d,\.]*)\s*(thousand|million|billion|trillion)?", lowered):
        cleaned = value_str.replace(",", "")
        if not cleaned or cleaned == ".":
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if magnitude:
            value *= scales[magnitude]
        # Canonical form: both "4.2e9" and "4200000000" collapse here.
        out.add(f"{value:.6g}")

    # Bare numbers with no magnitude word still count.
    for value_str in re.findall(r"\d[\d,\.]*", lowered):
        cleaned = value_str.replace(",", "")
        if not cleaned or cleaned == ".":
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if magnitude_free(cleaned, lowered):
            out.add(f"{value:.6g}")
    return out


def magnitude_free(cleaned: str, lowered: str) -> bool:
    """True when *cleaned* is not already the numeric part of a scaled phrase."""
    return not re.search(rf"{re.escape(cleaned)}\s*(?:thousand|million|billion|trillion)", lowered)


def _signature(tokens: Sequence[str]) -> set[str]:
    """Bag-of-words signature used for the semantic (paraphrase) track.

    Plain stemmed token set, not n-grams: n-grams over a sorted vocabulary are
    still positional, so one substituted word shifts every gram and the cosine
    collapses to 0.0 on exactly the reworded sentences this track exists to
    catch. A token-set cosine degrades gracefully with vocabulary overlap
    instead.
    """
    return {_stem(t) for t in tokens}


def _cosine(a: set[str], b: set[str]) -> float:
    """Cosine similarity over token sets (overlap coefficient, normalised)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    score: float = inter / ((len(a) ** 0.5) * (len(b) ** 0.5))
    return score


# ── Literal (6-level) matching ───────────────────────────────────────────────


def literal_match_level(candidate: str, reference: str) -> int:
    """Return the strongest matching level, or 0 for no match.

    5 exact · 4 numeric · 3 token-subset · 2 containment · 1 stemmed-Jaccard · 0 none
    """
    c, r = _normalise(candidate), _normalise(reference)
    if not c or not r:
        return 0
    if c == r:
        return 5

    c_nums, r_nums = _numbers(c), _numbers(r)
    if c_nums and c_nums == r_nums:
        return 4

    # Containment before token-subset: a subset match is strictly stronger, so
    # checking it first would make this level unreachable.
    if c in r or r in c:
        return 2

    c_tok, r_tok = _tokens(c), _tokens(r)
    if not c_tok or not r_tok:
        return 0

    c_set, r_set = set(c_tok), set(r_tok)
    if c_set and (c_set <= r_set or r_set <= c_set):
        return 3

    c_stem = {_stem(t) for t in c_tok}
    r_stem = {_stem(t) for t in r_tok}
    inter = len(c_stem & r_stem)
    if inter:
        jaccard = inter / len(c_stem | r_stem)
        if jaccard >= 0.5:
            return 1
    return 0


def matches(
    candidate: str,
    reference: str,
    *,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
) -> tuple[bool, str]:
    """Dual-track match: literal levels 1-5, else semantic cosine.

    Returns ``(matched, reason)`` so a score can always be explained.
    """
    level = literal_match_level(candidate, reference)
    if level >= 1:
        return True, f"literal_level_{level}"

    sim = _cosine(_signature(_tokens(candidate)), _signature(_tokens(reference)))
    if sim >= semantic_threshold:
        return True, f"semantic_{sim:.3f}"
    return False, "no_match"


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FactScore:
    """Key-Fact F1 with the raw counts kept for traceability."""

    precision: float
    recall: float
    f1: float
    matched: int
    predicted: int
    expected: int
    match_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.predicted == 0 or self.expected == 0


@dataclass(frozen=True)
class HallucinationScore:
    """Fraction of claims asserting an entity absent from the validated corpus."""

    rate: float
    hallucinated: int
    checked: int
    offending: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CitationScore:
    """Fraction of claims carrying at least one resolvable citation."""

    coverage: float
    cited: int
    total: int


# ── Metrics ──────────────────────────────────────────────────────────────────


def key_fact_f1(
    predicted: Iterable[str],
    gold: Iterable[str],
    *,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
) -> FactScore:
    """Dual-track Key-Fact F1 between predicted and gold statements.

    Each predicted statement is matched greedily against the best remaining gold
    statement (one-to-one), so a report cannot inflate recall by repeating the
    same fact several times.
    """
    pred_list = [p for p in (str(x).strip() for x in predicted) if p]
    gold_list = [g for g in (str(x).strip() for x in gold) if g]

    if not pred_list or not gold_list:
        return FactScore(
            precision=0.0 if pred_list else 1.0,
            recall=0.0,
            f1=0.0,
            matched=0,
            predicted=len(pred_list),
            expected=len(gold_list),
        )

    remaining = list(range(len(gold_list)))
    matched = 0
    reasons: dict[str, int] = {}

    for pred in pred_list:
        best_idx: int | None = None
        best_reason = ""
        for gi in remaining:
            ok, reason = matches(pred, gold_list[gi], semantic_threshold=semantic_threshold)
            # Prefer the strongest literal level among the candidates.
            if ok and (best_idx is None or _reason_rank(reason) > _reason_rank(best_reason)):
                best_idx, best_reason = gi, reason
        if best_idx is not None:
            remaining.remove(best_idx)
            matched += 1
            key = best_reason.split("_")[0] + ("_level" if "level" in best_reason else "")
            reasons[key] = reasons.get(key, 0) + 1

    precision = matched / len(pred_list)
    recall = matched / len(gold_list)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return FactScore(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        matched=matched,
        predicted=len(pred_list),
        expected=len(gold_list),
        match_reasons=reasons,
    )


def _reason_rank(reason: str) -> int:
    """Higher is a stronger match, used to pick the best gold candidate."""
    if reason.startswith("literal_level_"):
        return int(reason.rsplit("_", 1)[1])
    if reason.startswith("semantic_"):
        return 0
    return -1


def _claim_text(claim: Any) -> str:
    """Read a claim's text from either a model or a dict."""
    if isinstance(claim, str):
        return claim
    if isinstance(claim, dict):
        return str(claim.get("text", "") or "")
    return str(getattr(claim, "text", "") or "")


def hallucination_rate(
    claims: Iterable[Any],
    validated_corpus: str,
    *,
    extra_known_entities: Iterable[str] = (),
) -> HallucinationScore:
    """Fraction of claims asserting an entity the validated corpus never mentions.

    Only entities *present in the corpus* are considered: a claim about something
    the corpus never discusses is unsupported, not hallucinated, and counting it
    as a hallucination would conflate the two.

    Entities are extracted as capitalised multi-word spans, which is deliberately
    conservative — it under-counts rather than over-counting hallucinations.
    """
    corpus_tokens = {_stem(t) for t in _tokens(validated_corpus.lower())}
    known = {_stem(t) for t in _tokens(" ".join(extra_known_entities))}

    checked = 0
    hallucinated = 0
    offending: list[str] = []

    for claim in claims:
        text = _claim_text(claim)
        if not text:
            continue
        # Proper-noun spans: the entity vocabulary of the claim.
        spans = re.findall(r"\b([A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*)\b", text)
        entities = {s for s in spans if len(s) > 2}
        if not entities:
            continue

        checked += 1
        unknown = []
        for entity in entities:
            entity_stems = {_stem(t.lower()) for t in entity.split()}
            if not any(s in corpus_tokens or s in known for s in entity_stems):
                unknown.append(entity)
        if unknown:
            hallucinated += 1
            offending.extend(unknown[:3])

    rate = (hallucinated / checked) if checked else 0.0
    return HallucinationScore(
        rate=round(rate, 4),
        hallucinated=hallucinated,
        checked=checked,
        offending=offending[:10],
    )


def citation_coverage(claims: Iterable[Any]) -> CitationScore:
    """Fraction of claims carrying at least one resolvable http(s) citation.

    Claims arrive as live pydantic models during a run and as plain dicts
    after a Redis round-trip. Reading only the model attribute would report
    0% coverage for a job whose claims are all cited — the metric would look
    like a finding about the report when it is a bug in the metric.
    """
    total = 0
    cited = 0
    for claim in claims:
        total += 1
        citations = _claim_citations(claim)
        if any(_citation_url(c).startswith("http") for c in citations):
            cited += 1

    coverage = (cited / total) if total else 0.0
    return CitationScore(
        coverage=round(coverage, 4),
        cited=cited,
        total=total,
    )


def _claim_citations(claim: Any) -> list[Any]:
    """Read a claim's citations from either a model or a dict."""
    if isinstance(claim, dict):
        return list(claim.get("citations") or [])
    return list(getattr(claim, "citations", None) or [])


def _citation_url(citation: Any) -> str:
    """Read a citation's URL from either a model or a dict."""
    if isinstance(citation, dict):
        return str(citation.get("url", "") or "")
    return str(getattr(citation, "url", "") or "")
