"""
Passage evidence store: the run's retrieved page text, chunked and indexed.

Every claim in a report has to be traceable to text a page actually contains.
This module is the ground truth for that:

- ``chunk_page`` splits a scraped page into paragraph-aligned passages with
  stable ids and char offsets into the page, so a citation can point at an
  exact span rather than "somewhere on this URL".
- ``PassageIndex`` is a BM25 index over every passage the run has fetched,
  across all research loops. Extraction picks the passages of a page that are
  relevant to the question (instead of the page's first few thousand
  characters), and the verifier retrieves passages from *other* sources to
  check a claim against independent evidence.
- ``ground_quote`` checks that a quote the model attributes to a page really
  appears in it. A quote that cannot be found is not a citation.

Everything here is deterministic and free: no network, no LLM.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from searchops.knowledge.evaluation.relevance import _stem, _tokens

# A passage is roughly one or two paragraphs: big enough to carry a claim and
# its context, small enough that a handful fit in one prompt.
PASSAGE_TARGET_CHARS = 1_200
PASSAGE_MAX_CHARS = 2_000
# Pages are long-tailed (docs, papers). Index this much of each page; the
# relevant parts of a long page are found by retrieval, not by position.
MAX_PAGE_CHARS = 80_000
# Paragraphs shorter than this are almost always navigation or chrome.
_MIN_PARAGRAPH_CHARS = 40

_K1 = 1.5
_B = 0.75


class Passage(BaseModel):
    """A contiguous span of one scraped page."""

    id: str
    url: str
    title: str = ""
    text: str
    index: int = 0  # position of this passage within its page
    start: int = 0  # char offset of ``text`` within the (capped) page text


def passage_id(url: str, index: int) -> str:
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return f"p_{digest}_{index}"


def _looks_like_chrome(paragraph: str) -> bool:
    """Navigation lists, link farms and cookie banners carry no evidence."""
    stripped = paragraph.strip()
    if len(stripped) < _MIN_PARAGRAPH_CHARS:
        return True
    words = stripped.split()
    if len(words) < 6:
        return True
    # Markdown link farms: most of the paragraph is [text](url) syntax.
    link_chars = sum(len(m.group(0)) for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", stripped))
    return link_chars > 0.6 * len(stripped)


def chunk_page(url: str, title: str, text: str) -> list[Passage]:
    """Split page text into paragraph-aligned passages with stable ids."""
    text = (text or "")[:MAX_PAGE_CHARS]
    if not text.strip():
        return []

    passages: list[Passage] = []
    buf: list[tuple[int, str]] = []  # (start offset, paragraph)
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        start = buf[0][0]
        body = "\n\n".join(p for _, p in buf).strip()
        if body:
            idx = len(passages)
            passages.append(Passage(id=passage_id(url, idx), url=url, title=title, text=body, index=idx, start=start))
        buf, buf_len = [], 0

    for match in re.finditer(r"(?:[^\n]|\n(?!\s*\n))+", text):
        para = match.group(0).strip()
        if _looks_like_chrome(para):
            continue
        # Hard-split a single oversized paragraph on sentence boundaries.
        pieces = [para] if len(para) <= PASSAGE_MAX_CHARS else _split_long(para)
        offset = match.start()
        for piece in pieces:
            if buf_len + len(piece) > PASSAGE_TARGET_CHARS and buf:
                flush()
            buf.append((offset, piece))
            buf_len += len(piece)
            offset += len(piece)
    flush()
    return passages


def _split_long(paragraph: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) > PASSAGE_TARGET_CHARS and cur:
            out.append(cur.strip())
            cur = ""
        cur += s + " "
        while len(cur) > PASSAGE_MAX_CHARS:
            out.append(cur[:PASSAGE_MAX_CHARS])
            cur = cur[PASSAGE_MAX_CHARS:]
    if cur.strip():
        out.append(cur.strip())
    return out


def _terms(text: str) -> list[str]:
    return [_stem(t) for t in _tokens(text)]


@dataclass
class PassageIndex:
    """BM25 index over every passage the run has retrieved."""

    passages: list[Passage]
    _tf: list[dict[str, int]] = field(init=False, default_factory=list)
    _len: list[int] = field(init=False, default_factory=list)
    _df: dict[str, int] = field(init=False, default_factory=dict)
    _avg: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        for p in self.passages:
            counts: dict[str, int] = {}
            for t in _terms(p.text):
                counts[t] = counts.get(t, 0) + 1
            self._tf.append(counts)
            self._len.append(sum(counts.values()))
            for t in counts:
                self._df[t] = self._df.get(t, 0) + 1
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0

    def __len__(self) -> int:
        return len(self.passages)

    def _idf(self, term: str) -> float:
        n = len(self.passages)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, i: int) -> float:
        q_terms = set(_terms(query))
        tf, dl = self._tf[i], self._len[i] or 1
        avg = self._avg or dl
        total = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if f:
                total += self._idf(t) * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / avg))
        return total

    def search(
        self,
        query: str,
        k: int = 5,
        *,
        urls: set[str] | None = None,
        exclude_urls: set[str] | None = None,
        min_score: float = 0.0,
    ) -> list[tuple[Passage, float]]:
        """Top-*k* passages for *query*, optionally restricted by source URL."""
        if not self.passages or not query.strip():
            return []
        scored: list[tuple[Passage, float]] = []
        for i, p in enumerate(self.passages):
            if urls is not None and p.url not in urls:
                continue
            if exclude_urls and p.url in exclude_urls:
                continue
            s = self.score(query, i)
            if s > min_score:
                scored.append((p, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def by_id(self) -> dict[str, Passage]:
        return {p.id: p for p in self.passages}


# ── Quote grounding ──────────────────────────────────────────────────────────


# Curly quotes and en/em dashes fold to their ASCII forms before matching.
_TYPOGRAPHIC = str.maketrans(
    {
        "‘": "'",  # noqa: RUF001
        "’": "'",  # noqa: RUF001
        "‚": "'",  # noqa: RUF001
        "‛": "'",  # noqa: RUF001
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "–": "-",  # noqa: RUF001
        "—": "-",
    }
)


def _norm(text: str) -> str:
    text = text.lower()
    text = text.translate(_TYPOGRAPHIC)
    text = re.sub(r"[*_`#>\[\]]", "", text)  # markdown emphasis/link syntax
    return re.sub(r"\s+", " ", text).strip()


def ground_quote(quote: str, source: str) -> str | None:
    """Return the quote if it occurs in *source*, else ``None``.

    Matching is whitespace/case/markdown-insensitive, so a quote copied from a
    rendered page still matches its markdown. A quote of which only a long
    contiguous run of words (>= 8 words, >= 60% of the quote) appears is
    accepted as the matching run — models often trim or join the ends of a
    sentence. Anything shorter is rejected: a citation must be checkable.
    """
    q = _norm(quote or "")
    if len(q) < 12:
        return None
    s = _norm(source or "")
    if q in s:
        return quote.strip()
    words = q.split()
    n = len(words)
    min_run = max(8, math.ceil(0.6 * n))
    if n < min_run:
        return None
    for size in range(n - 1, min_run - 1, -1):
        for start in range(0, n - size + 1):
            run = " ".join(words[start : start + size])
            if run in s:
                return run
    return None


def claim_support_overlap(claim: str, passage: str) -> float:
    """Fraction of the claim's content terms present in *passage* (0..1)."""
    c = set(_terms(claim))
    if not c:
        return 0.0
    p = set(_terms(passage))
    return len(c & p) / len(c)


def passages_from_state(raw: list[Any]) -> list[Passage]:
    """Accept passages as models or plain dicts (state restored from JSON)."""
    out: list[Passage] = []
    for item in raw or []:
        if isinstance(item, Passage):
            out.append(item)
        elif isinstance(item, dict):
            with contextlib.suppress(ValueError):  # a malformed record is not evidence
                out.append(Passage.model_validate(item))
    return out
