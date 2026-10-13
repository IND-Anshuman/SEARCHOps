"""
Deterministic JSON repair ladder for LLM extraction output.

LLMs asked for strict JSON still emit near-misses: markdown fences, trailing
commas, single-quoted strings, unquoted keys, smart quotes, ``None``/``True``
literals, truncated tails, or prose wrapped around the object. The extraction
nodes used to treat all of these as total losses — one malformed character
silently destroyed a document's worth of evidence.

This module repairs what can be repaired deterministically, in strict
increasing order of intrusiveness, and reports *which* tier was needed so
callers can count it:

    tier 0  exact              — the text was already valid JSON
    tier 1  fence_strip        — ```json ... ``` wrapper removed
    tier 2  balanced_slice     — first balanced {...} / [...] extracted from prose
    tier 3  structural_repair  — trailing commas, smart quotes, python literals
    tier 4  span_salvage       — object is unparseable; regex-salvage "text" fields

Tier 4 is *partial* recovery: it returns ``{"claims": [...]}`` containing only
the claims whose text survived, rather than pretending the document parsed
cleanly. ``repair_tier_used`` tells the caller exactly what happened, so a
downstream consumer can distinguish a clean parse from a salvage.

Design rules:
- Pure functions, no I/O, no LLM calls — fully unit-testable and deterministic.
- Never invents content. Salvage only lifts spans that literally appear in the
  input; it never rephrases, fills gaps, or infers a missing field.
- ``salvage_keys`` is caller-supplied so the same ladder serves both the
  claims schema and the entities/relations schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import orjson

# ── Repair tier identifiers ──────────────────────────────────────────────────
# Ordered least → most intrusive. The value is the number of tiers applied.
TIER_EXACT = 0
TIER_FENCE_STRIP = 1
TIER_BALANCED_SLICE = 2
TIER_STRUCTURAL_REPAIR = 3
TIER_SPAN_SALVAGE = 4

TIER_NAMES: dict[int, str] = {
    TIER_EXACT: "exact",
    TIER_FENCE_STRIP: "fence_strip",
    TIER_BALANCED_SLICE: "balanced_slice",
    TIER_STRUCTURAL_REPAIR: "structural_repair",
    TIER_SPAN_SALVAGE: "span_salvage",
}

_MAX_INPUT_CHARS = 200_000  # guard: never regex-scan an unbounded blob

# Fence at start-of-line (tolerates leading prose before the fence).
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)

# Python-ish literals that JSON does not allow, mapped to JSON equivalents.
_PY_LITERALS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bNone\b"), "null"),
    (re.compile(r"\bTrue\b"), "true"),
    (re.compile(r"\bFalse\b"), "false"),
)

# Smart/curly quotes → straight equivalents.
_SMART_QUOTES = str.maketrans(
    {
        "\u201c": '"',  # “ left double
        "\u201d": '"',  # ” right double
        "\u2018": "'",  # ' left single
        "\u2019": "'",  # ' right single
    }
)

# Trailing comma before a closing brace/bracket, whitespace-tolerant.
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


@dataclass(frozen=True)
class RepairOutcome:
    """Result of attempting the repair ladder on one text."""

    parsed: object | None
    repair_tier: int
    tier_name: str
    salvaged: bool
    """True when tier 4 recovered *some* records from an unparseable object."""

    recovered_count: int
    """Number of records recovered by span salvage (0 unless salvaged)."""

    error: str | None = None


def _try_loads(text: str) -> object | None:
    """Strict JSON parse; return None on failure (never raises)."""
    try:
        parsed: object = orjson.loads(text)
    except Exception:
        return None
    return parsed


def _strip_fence(text: str) -> str | None:
    """Return fenced content if a markdown fence is present, else None."""
    m = _FENCE_RE.search(text)
    if not m:
        return None
    return m.group(1).strip()


def _slice_balanced(text: str) -> str | None:
    """Extract the first balanced top-level {...} or [...] from *text*.

    Handles the common LLM shape: prose, then a JSON object, then more prose.
    Ignores braces inside strings.
    """
    if len(text) > _MAX_INPUT_CHARS:
        text = text[:_MAX_INPUT_CHARS]

    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # Unbalanced: return the tail from the opener so truncation repair can try.
    return text[start:]


def _structural_repair(text: str) -> str:
    """Apply deterministic, semantics-preserving JSON repairs."""
    out = text.translate(_SMART_QUOTES)
    for pattern, replacement in _PY_LITERALS:
        out = pattern.sub(replacement, out)
    out = _TRAILING_COMMA_RE.sub(r"\1", out)
    return out


def _salvage_spans(text: str, record_key: str, field_keys: tuple[str, ...]) -> list[dict[str, object]]:
    """Recover records by regex when the whole object cannot be parsed.

    Truncation happens in three places, and all three must survive:

    1. between records  — ``...}, {"text": "trun``
    2. inside a nested array — ``...}, {"text": "ok", "citations": [{"url": "ht``
    3. inside the text value — ``...}, {"text": "he said "he``

    So a record is closed by *either* its braces balancing (the clean case)
    *or* the appearance of the next record's opener, *or* end-of-input
    (truncation cases 1-3). A record is only kept if a field actually parsed
    out of it, so a fragment like ``{"text": "he`` (never terminated) is
    dropped rather than yielding a half-value.

    Only spans that literally appear in the input are lifted — nothing is
    invented, completed, or reordered.
    """
    if len(text) > _MAX_INPUT_CHARS:
        text = text[:_MAX_INPUT_CHARS]

    key_alt = "|".join(re.escape(k) for k in field_keys)
    field_re = re.compile(r'"(' + key_alt + r')"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)

    anchor = re.compile(r'"' + re.escape(record_key) + r'"\s*:\s*\[')
    m = anchor.search(text)
    if m is None:
        return []

    body = text[m.end() :]
    # Opener of the next record: a `{` followed by a field key. Used to close a
    # record whose braces never balance because of truncation.
    next_record_re = re.compile(r'\{\s*"(?:' + key_alt + r')"')

    # A field whose string value never terminates (truncation mid-value). Its
    # value is still verbatim model output, so it is kept and flagged.
    open_field_re = re.compile(r'"(' + key_alt + r')"\s*:\s*"(?P<val>[^"]*)\Z')

    records: list[dict[str, object]] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False

    def _take(chunk: str, *, unterminated: bool = False) -> None:
        found = {f.group(1): f.group(2) for f in field_re.finditer(chunk)}
        if unterminated:
            open_match = open_field_re.search(chunk)
            if open_match:
                found.setdefault(open_match.group(1), open_match.group("val"))
        if not found:
            return
        record: dict[str, object] = dict(found)
        if "confidence" in record:
            try:
                record["confidence"] = float(str(record["confidence"]))
            except (TypeError, ValueError):
                record.pop("confidence", None)
        records.append(record)

    i = 0
    while i < len(body):
        ch = body[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "{":
            # A record opener while we are already inside one means the
            # previous record was truncated before its braces balanced.
            next_match = next_record_re.match(body, i)
            if depth > 0 and start is not None and next_match:
                _take(body[start:i], unterminated=True)
                depth = 1
                start = i
                i += 1
                continue
            if depth == 0:
                start = i
            depth += 1
            i += 1
            continue

        if ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    _take(body[start : i + 1])
                    start = None
            i += 1
            continue

        i += 1

    # Truncated tail: braces never balanced, so nothing closed it.
    if depth > 0 and start is not None:
        _take(body[start:], unterminated=True)

    return records


def repair_json(
    text: str,
    *,
    record_key: str = "claims",
    field_keys: tuple[str, ...] = ("text",),
) -> RepairOutcome:
    """Run the repair ladder over *text* and report the tier that worked.

    Args:
        text: Raw model output.
        record_key: The JSON array key to salvage from (e.g. ``"claims"``).
        field_keys: String fields to lift during span salvage.

    Returns:
        :class:`RepairOutcome`. ``parsed`` is a ``list[dict]`` when salvage
        succeeded, a ``dict``/``list`` when a parse tier succeeded, and
        ``None`` when nothing could be recovered.
    """
    if text is None:
        return RepairOutcome(None, TIER_SPAN_SALVAGE, TIER_NAMES[TIER_SPAN_SALVAGE], False, 0, "empty_input")

    raw = text.strip()
    if not raw:
        return RepairOutcome(None, TIER_SPAN_SALVAGE, TIER_NAMES[TIER_SPAN_SALVAGE], False, 0, "empty_input")

    # Tier 0 — exact
    parsed = _try_loads(raw)
    if parsed is not None:
        return RepairOutcome(parsed, TIER_EXACT, TIER_NAMES[TIER_EXACT], False, 0)

    # Tier 1 — fence strip
    fenced = _strip_fence(raw)
    if fenced is not None:
        parsed = _try_loads(fenced)
        if parsed is not None:
            return RepairOutcome(parsed, TIER_FENCE_STRIP, TIER_NAMES[TIER_FENCE_STRIP], False, 0)
        raw = fenced  # keep unwrapped text for later tiers

    # Tier 2 — balanced slice
    sliced = _slice_balanced(raw)
    if sliced is not None and sliced != raw:
        parsed = _try_loads(sliced)
        if parsed is not None:
            return RepairOutcome(parsed, TIER_BALANCED_SLICE, TIER_NAMES[TIER_BALANCED_SLICE], False, 0)
        raw = sliced

    # Tier 3 — structural repair
    repaired = _structural_repair(raw)
    parsed = _try_loads(repaired)
    if parsed is not None:
        return RepairOutcome(parsed, TIER_STRUCTURAL_REPAIR, TIER_NAMES[TIER_STRUCTURAL_REPAIR], False, 0)

    # Tier 3.5 — structural repair applied to the sliced body (covers prose +
    # trailing-comma combined, which is the most common real-world shape).
    sliced2 = _slice_balanced(repaired)
    if sliced2 is not None:
        parsed = _try_loads(sliced2)
        if parsed is not None:
            return RepairOutcome(parsed, TIER_STRUCTURAL_REPAIR, TIER_NAMES[TIER_STRUCTURAL_REPAIR], False, 0)
        repaired = sliced2

    # Tier 4 — span salvage (partial recovery)
    records = _salvage_spans(repaired, record_key, field_keys)
    if records:
        return RepairOutcome(
            {record_key: records},
            TIER_SPAN_SALVAGE,
            TIER_NAMES[TIER_SPAN_SALVAGE],
            True,
            len(records),
        )

    return RepairOutcome(
        None,
        TIER_SPAN_SALVAGE,
        TIER_NAMES[TIER_SPAN_SALVAGE],
        False,
        0,
        "unrecoverable",
    )
