"""Deterministic clean-up of model-written report sections.

Prompts ask for clean structure; this module guarantees it. A model's section
routinely arrives with habits that make a report read as clutter:

- citation runs (``[1][2][3][4][5][6]``) after every sentence, and the same
  marker repeated on five consecutive sentences;
- its own ``## Title`` or ``# Heading`` fighting the report's hierarchy;
- a private "References" list appended to the section;
- a list or table glued to the paragraph above it, so the block parser sees
  one paragraph full of pipes and dashes;
- ragged tables (rows with more or fewer cells than the header);
- paragraphs of eight sentences;
- the whole reply wrapped in a ```markdown fence.

Every rule here is mechanical and conservative: text is moved, merged or
de-duplicated, never invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_REFS_PER_MARK = 3
_MAX_PARAGRAPH_SENTENCES = 5

_REF_RUN = re.compile(r"(?:\s*\[\d{1,3}\](?!\())+")
_SOURCES_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*|\*\*)?(?:sources|references|bibliography|works cited|citations)\b[:*\s]*$",
    re.IGNORECASE,
)
_TAKEAWAY = re.compile(
    r"^\s*>?\s*\**\s*(?:key takeaway|bottom line|takeaway|in short)\s*:?\s*\**\s*:?\s*(.+)$",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(*])")
_TRAILING_REFS = re.compile(r"((?:\s*\[\d{1,3}\])+)\s*([.!?][\"')]*)?\s*$")
_FILLER = re.compile(
    r"\b(?:(?:In conclusion|In summary|To summari[sz]e|Overall),|It is (?:important|worth) (?:to note|noting) that)\s+",
)


@dataclass
class PolishedSection:
    body: str
    takeaway: str  # one sentence, may be ""
    # False when the model wrote no takeaway line and it was taken from the
    # body's first sentence — it must not then be rendered a second time.
    takeaway_explicit: bool = True


def polish_section(text: str, section_name: str) -> PolishedSection:
    text = _unfence(text.replace("\r\n", "\n")).strip()
    lines = text.split("\n")
    lines = _drop_leading_title(lines, section_name)
    lines = _cut_reference_list(lines)
    lines = [ln for ln in lines if not re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", ln)]

    takeaway, lines = _extract_takeaway(lines)
    lines = [_demote_heading(ln) for ln in lines]
    body = "\n".join(_separate_blocks(lines))

    chunks = [c for c in re.split(r"\n\s*\n", body) if c.strip()]
    out: list[str] = []
    for chunk in chunks:
        kind = _chunk_kind(chunk)
        if kind == "table":
            out.append(fix_table(chunk))
        elif kind == "paragraph":
            out.extend(_split_long_paragraph(_dedupe_paragraph_refs(_defiller(chunk))))
        else:
            out.append(chunk)
    out = _drop_closing_summary(out)
    body = "\n\n".join(normalise_refs(c) for c in out).strip()
    takeaway = normalise_refs(_defiller(takeaway)).strip()
    if not takeaway:
        return PolishedSection(body=body, takeaway=_first_sentence(body), takeaway_explicit=False)
    return PolishedSection(body=body, takeaway=takeaway)


# ── Citations ────────────────────────────────────────────────────────────────


def validate_citations(text: str, valid: set[int]) -> tuple[str, int]:
    """Normalise ``[n]`` markers and drop any that do not resolve.

    ``[2, 5]`` becomes ``[2][5]``; a number outside the registry is removed.
    Returns the cleaned text and how many markers were dropped.
    """
    dropped = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal dropped
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        keep = [n for n in nums if n in valid]
        dropped += len(nums) - len(keep)
        return "".join(f"[{n}]" for n in keep)

    # Leave Markdown links ``[text](url)`` alone: only bare numeric brackets.
    cleaned = re.sub(r"\[(\d{1,3}(?:\s*[,;–-]\s*\d{1,3})*)\](?!\()", _sub, text)  # noqa: RUF001
    return cleaned, dropped


def normalise_refs(text: str) -> str:
    """Collapse each run of ``[n]`` markers: unique, at most three, before the full stop."""

    def _run(m: re.Match[str]) -> str:
        nums = list(dict.fromkeys(int(n) for n in re.findall(r"\d+", m.group(0))))
        return " " + "".join(f"[{n}]" for n in nums[:MAX_REFS_PER_MARK])

    text = _REF_RUN.sub(_run, text)
    # "fact. [3]" → "fact [3]."
    text = re.sub(r"([.;,])((?: ?\[\d{1,3}\])+)(?=\s|$)", r"\2\1", text)
    text = re.sub(r"[ \t]+(?=[.;,](?:\s|$))", "", text)
    return re.sub(r"(?<=\S)[ \t]{2,}", " ", text)


def _dedupe_paragraph_refs(paragraph: str) -> str:
    """Keep a marker once per run of consecutive sentences that share it."""
    sentences = _SENTENCE_SPLIT.split(paragraph.strip())
    if len(sentences) < 2:
        return paragraph
    refs = []
    for s in sentences:
        m = _TRAILING_REFS.search(s)
        refs.append(frozenset(re.findall(r"\d+", m.group(1))) if m else frozenset())
    for i in range(len(sentences) - 1):
        if refs[i] and refs[i] == refs[i + 1]:
            sentences[i] = _TRAILING_REFS.sub(lambda m: m.group(2) or "", sentences[i]).rstrip()
            if sentences[i] and sentences[i][-1] not in ".!?\"')":
                sentences[i] += "."
    return " ".join(sentences)


# ── Structure ────────────────────────────────────────────────────────────────


def _unfence(text: str) -> str:
    m = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else text


def _bare(line: str) -> str:
    return re.sub(r"^\s*>?\s*#*\s*", "", line).strip("*_: ").strip().lower()


def _drop_leading_title(lines: list[str], section_name: str) -> list[str]:
    """Remove an echoed section title (as heading or bold line) near the top."""
    target = section_name.strip().strip("*#_ ").lower()
    head = [ln for ln in lines[:5] if not (ln.strip() and _bare(ln) == target)]
    lines = head + lines[5:]
    # A # or ## heading opening the reply is the model re-titling the part;
    # the report already gives it its heading.
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None and re.match(r"^\s*#{1,2}\s", lines[first]):
        lines = lines[:first] + lines[first + 1 :]
    return lines


_WRAPUP_HEADING = re.compile(
    r"^\s*#{1,6}\s*\**\s*(?:conclusions?|summary|in summary|final thoughts|wrap[- ]up|closing thoughts)\s*\**\s*:?\s*$",
    re.IGNORECASE,
)


def _cut_reference_list(lines: list[str]) -> list[str]:
    """Drop a model-written references list; the report has one Sources list."""
    for i, line in enumerate(lines):
        if i > 0 and _SOURCES_HEADING.match(line):
            lines = lines[:i]
            break
    return _cut_wrapup(lines)


def _cut_wrapup(lines: list[str]) -> list[str]:
    """Drop a per-part "Conclusion" subsection; the report concludes once, at its end."""
    out: list[str] = []
    skipping = False
    for line in lines:
        if _WRAPUP_HEADING.match(line):
            skipping = True
            continue
        if skipping and re.match(r"^\s*#{1,6}\s", line):
            skipping = False
        if not skipping:
            out.append(line)
    return out


def _extract_takeaway(lines: list[str]) -> tuple[str, list[str]]:
    for i, line in enumerate(lines[:6]):
        m = _TAKEAWAY.match(line)
        if m and len(m.group(1).strip("* ")) > 10:
            sentence = m.group(1).strip().strip("*").strip()
            # A quote block can wrap; absorb continuation lines.
            j = i + 1
            while j < len(lines) and lines[j].startswith(">"):
                sentence += " " + lines[j].lstrip("> ").strip()
                j += 1
            return " ".join(sentence.split()), lines[:i] + lines[j:]
    return "", lines


def _demote_heading(line: str) -> str:
    m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
    if not m:
        return line
    level = len(m.group(1))
    title = m.group(2).strip().strip("*").strip()
    level = 3 if level <= 3 else 4
    return f"{'#' * level} {title}"


def _line_kind(line: str) -> str:
    s = line.strip()
    if not s:
        return "blank"
    if s.startswith("#"):
        return "heading"
    if s.startswith("|"):
        return "table"
    if s.startswith(">"):
        return "quote"
    if re.match(r"^(?:[-*+•]\s|\d+[.)]\s)", s):
        return "list"
    if line.startswith(("  ", "\t")):
        return "indent"
    return "text"


def _separate_blocks(lines: list[str]) -> list[str]:
    """Put a blank line wherever the block type changes, so each block parses alone."""
    out: list[str] = []
    prev = "blank"
    for line in lines:
        kind = _line_kind(line)
        if kind == "indent":
            kind = prev if prev in ("list", "text") else "text"
        if kind != "blank" and prev != "blank" and (kind != prev or kind == "heading"):
            out.append("")
        if kind == "list":
            line = re.sub(r"^(\s*)[*+•]\s", r"\1- ", line)
        out.append(line.rstrip())
        prev = kind
    return out


def _chunk_kind(chunk: str) -> str:
    kind = _line_kind(chunk.split("\n", 1)[0])
    return "paragraph" if kind in ("text", "indent") else kind


def fix_table(chunk: str) -> str:
    """Make a Markdown table well-formed: separator row present, every row the header's width."""
    rows = []
    for line in chunk.strip().split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{1,}:?", c) for c in cells if c) and any(cells):
            continue
        if any(cells):
            rows.append(cells)
    if not rows:
        return chunk
    width = len(rows[0])
    norm = [(r + [""] * width)[:width] for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


_CLOSING_OPENER = re.compile(
    r"^\s*(?:these (?:points|findings|figures|details)|the (?:comparison|table|analysis|evidence) (?:shows|above|indicates|suggests)"
    r"|this (?:section|part|overview|comparison)|overall|in summary|in conclusion|to summari[sz]e|together,|taken together"
    r"|each of these|ultimately|the choice (?:between|should|depends))",
    re.IGNORECASE,
)


def _drop_closing_summary(chunks: list[str]) -> list[str]:
    """Drop an uncited wrap-up paragraph at the end of a part.

    Models close a part with "These points provide a quick snapshot..." or
    "The comparison shows each has distinct strengths...". It restates what was
    just said, cites nothing, and the report has its own summary and closing.
    A final paragraph that carries citations is real content and stays.
    """
    if len(chunks) > 1 and _chunk_kind(chunks[-1]) == "paragraph":
        last = chunks[-1]
        if not re.search(r"\[\d{1,3}\]", last) and _CLOSING_OPENER.match(last):
            return chunks[:-1]
    return chunks


def _split_long_paragraph(paragraph: str) -> list[str]:
    sentences = _SENTENCE_SPLIT.split(paragraph.strip())
    if len(sentences) <= _MAX_PARAGRAPH_SENTENCES:
        return [paragraph.strip()]
    out = []
    step = (len(sentences) + 1) // 2 if len(sentences) <= 2 * _MAX_PARAGRAPH_SENTENCES else 4
    for i in range(0, len(sentences), step):
        out.append(" ".join(sentences[i : i + step]))
    return out


def _defiller(text: str) -> str:
    """Drop stock openers ("In conclusion, ...") and re-capitalise what follows."""
    marked = _FILLER.sub("\x00", text)
    return re.sub(r"\x00(\w)?", lambda m: (m.group(1) or "").upper(), marked)


def _first_sentence(body: str) -> str:
    for chunk in re.split(r"\n\s*\n", body):
        if _chunk_kind(chunk) == "paragraph":
            return _SENTENCE_SPLIT.split(chunk.strip())[0]
    return ""
