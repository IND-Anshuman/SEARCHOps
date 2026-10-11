"""
Report shaping and validation for user-facing research output.

The report writer sends one LLM call and previously piped the raw reply
straight into ``final_report`` with no validation. In practice an LLM reply can
be:

- empty or whitespace (model refusal, provider truncation, swallowed error)
- a bare refusal line ("I'm sorry, I can't help with that")
- valid prose but with no structure — an unreadable wall of text
- a good report whose *evidence* never made it into the prompt

Any of those reaches the user as a broken product. This module shapes the
report into a predictable, readable structure and tells the caller exactly what
was wrong so the caller can act on it instead of shipping a blank screen.

Guarantees this module makes:

1. **Never returns empty.** If the LLM produced nothing usable, a structured
   report is assembled from the evidence that *did* exist (claims, entities,
   sources), so the user always sees what was actually found.
2. **Always has sections.** A sectionless report is given a synthesized
   Executive Summary so the reader is never handed a wall of text.
3. **Citations always resolve.** Every cited URL that appears in the evidence is
   listed; claims are attributed to the source that produced them.
4. **Honest degradation.** ``ReportQuality`` records what actually happened
   (``raw_empty``, ``refusal``, ``no_sections``, ``synthesized_from_evidence``,
   ``ok``) so the UI can tell the user why a report looks the way it does.

Nothing here fabricates content: when the report is synthesized from evidence,
every line is derived from real extracted claims or entities. If there is no
evidence either, the report says so plainly rather than inventing findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Phrases that mean the model declined rather than answered. Matched
# case-insensitively against a short report body.
#
# "as an ai" is deliberately NOT a refusal marker: models routinely open a
# perfectly good report with "As an AI language model, I summarised the
# sources below", and destroying a real report over that phrasing loses
# evidence. Refusals are reliably signalled by an inability statement instead.
_REFUSAL_MARKERS: tuple[str, ...] = (
    "i'm sorry",
    "i am sorry",
    "i cannot",
    "i can't",
    "i'm unable",
    "i am unable",
    "i don't have access",
    "i do not have access",
    "unable to provide",
    "cannot provide",
    "cannot assist",
    "can't assist",
    "no information available",
)

# A report shorter than this (after trimming) is treated as empty.
_MIN_REPORT_CHARS = 40

# A body at or above this length is treated as a substantive answer, even if it
# opens with an apologetic phrase. Kept well above any refusal, and below any
# real report, so the two never overlap.
_SUBSTANTIVE_CHARS = 300

# Canonical section titles, in the order a reader expects.
_SECTION_ORDER: tuple[str, ...] = (
    "Executive Summary",
    "Key Findings",
    "Notable Entities",
    "Sources",
    "Conclusion",
)


class ReportQuality(StrEnum):
    """Why a report looks the way it does — surfaced to the user, never hidden."""

    OK = "ok"
    """The model produced a usable report; no intervention needed."""

    RAW_EMPTY = "raw_empty"
    """The model returned nothing usable; report synthesized from evidence."""

    REFUSAL = "refusal"
    """The model declined the request; report synthesized from evidence."""

    NO_SECTIONS = "no_sections"
    """The model returned prose with no structure; sections were added."""

    NO_EVIDENCE = "no_evidence"
    """Neither the model nor the evidence produced anything. Stated plainly."""

    SYNTHESIZED = "synthesized"
    """Report assembled entirely from extracted evidence."""


@dataclass
class ShapedReport:
    """A validated, readable report plus the provenance of its shaping."""

    markdown: str
    quality: ReportQuality
    sections: list[str] = field(default_factory=list)
    citation_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_synthesized(self) -> bool:
        """True when the visible report was not written by the model."""
        return self.quality in (
            ReportQuality.RAW_EMPTY,
            ReportQuality.REFUSAL,
            ReportQuality.SYNTHESIZED,
            ReportQuality.NO_EVIDENCE,
        )

    @property
    def user_message(self) -> str:
        """Plain-language explanation for the UI banner.

        Deliberately free of internal jargon: it says what the user gets, not
        how the report was assembled.
        """
        return {
            ReportQuality.OK: "",
            ReportQuality.RAW_EMPTY: "The model returned no text, so this report was built from the sources that were found.",
            ReportQuality.REFUSAL: "The model declined to summarise this query, so this report was built from the sources that were found.",
            ReportQuality.NO_SECTIONS: "The model's reply had no structure, so headings were added for readability.",
            ReportQuality.NO_EVIDENCE: "No sources could be read for this query, so there is nothing to report yet.",
            ReportQuality.SYNTHESIZED: "This report was assembled directly from the extracted findings.",
        }[self.quality]


# ── Detection helpers ────────────────────────────────────────────────────────


def looks_like_refusal(text: str) -> bool:
    """True when *text* is a model refusal rather than an answer.

    A refusal is short and matches a marker. A real report may *open* with an
    apologetic phrase and still carry substance, so a body at or above
    ``_SUBSTANTIVE_CHARS`` is always treated as an answer. Below that, a marker
    match means the model declined and the caller should synthesize from
    evidence instead of showing the refusal to the user.

    No minimum-length guard lives here: "empty" is the caller's concern, and
    gating on it here would let short refusals fall through undetected.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) >= _SUBSTANTIVE_CHARS:
        return False  # substantive output, regardless of opening phrasing
    lowered = stripped.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def extract_sections(markdown: str) -> list[str]:
    """Return the headings present in *markdown*, in document order."""
    return [line.lstrip("#").strip() for line in markdown.splitlines() if line.lstrip().startswith("#")]


# ── Evidence rendering ───────────────────────────────────────────────────────


def _claim_text(claim: Any) -> str:
    return str(getattr(claim, "text", "") or "").strip()


def _claim_url(claim: Any) -> str:
    citations = getattr(claim, "citations", None) or []
    for citation in citations:
        url = str(getattr(citation, "url", "") or "")
        if url.startswith("http"):
            return url
    return ""


def _entity_line(entity: Any) -> str:
    name = str(getattr(entity, "name", "") or "").strip()
    if not name:
        return ""
    etype = str(getattr(entity, "entity_type", "") or "Concept")
    desc = str(getattr(entity, "description", "") or "").strip()
    return f"- **{name}** ({etype})" + (f" — {desc}" if desc else "")


def _source_line(url: str, title: str) -> str:
    label = title.strip() or url
    return f"- [{label}]({url})"


def build_evidence_report(
    query: str,
    claims: list[Any] | None = None,
    entities: list[Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Assemble a readable Markdown report purely from real evidence.

    Used when the model produced nothing usable. Every line traces to an
    extracted claim, a real entity, or a real source URL — nothing is invented.
    When there is no evidence at all, the report says so plainly.
    """
    claims = claims or []
    entities = entities or []
    sources = sources or []

    parts: list[str] = [f"# Research Report: {query}" if query else "# Research Report", ""]

    # ── Executive summary ────────────────────────────────────────────────
    parts.append("## Executive Summary")
    parts.append("")
    if claims or entities:
        n_claims = len(claims)
        n_sources = len(sources)
        noun_c = "claim" if n_claims == 1 else "claims"
        noun_s = "source" if n_sources == 1 else "sources"
        found = f"**{n_claims} {noun_c}** from **{n_sources} {noun_s}**"
        parts.append(
            f"This report was assembled directly from the extracted evidence rather than a model "
            f"summary. The analysis below is drawn from {found}."
        )
    elif sources:
        # Sources were retrieved and read, but no discrete claims came out of
        # them. Saying "no sources could be read" here would contradict the
        # Sources section listing them, so state what actually happened.
        n_sources = len(sources)
        noun_s = "source" if n_sources == 1 else "sources"
        parts.append(
            f"**{n_sources} {noun_s}** were retrieved and read for this query, but no "
            f"individual factual claims could be extracted from them. The pages are "
            f"listed under Sources — open them directly to review what was found."
        )
    else:
        parts.append(
            "No sources could be read for this query, so no findings are available. "
            "The search did not return readable content — try rephrasing the query or "
            "widening the research depth."
        )
    parts.append("")

    # ── Key findings (real claims only) ──────────────────────────────────
    parts.append("## Key Findings")
    parts.append("")
    usable_claims = [c for c in claims if _claim_text(c)]
    if usable_claims:
        for claim in usable_claims:
            url = _claim_url(claim)
            text = _claim_text(claim)
            confidence = float(getattr(claim, "confidence", 0.0) or 0.0)
            suffix = f" [source]({url})" if url else ""
            if confidence >= 0.8:
                suffix += " _(high confidence)_"
            parts.append(f"- {text}{suffix}")
    else:
        parts.append("- No factual claims could be extracted from the available sources.")
    parts.append("")

    # ── Notable entities ─────────────────────────────────────────────────
    if entities:
        parts.append("## Notable Entities")
        parts.append("")
        for entity in entities:
            line = _entity_line(entity)
            if line:
                parts.append(line)
        parts.append("")

    # ── Sources (real URLs only) ─────────────────────────────────────────
    parts.append("## Sources")
    parts.append("")
    seen_urls: set[str] = set()
    for src in sources:
        url = str(src.get("url", "") or "").strip()
        if not url.startswith("http") or url in seen_urls:
            continue
        seen_urls.add(url)
        parts.append(_source_line(url, str(src.get("title", "") or "")))
    if not seen_urls:
        parts.append("- No sources were retrieved.")
    parts.append("")

    parts.append("## Conclusion")
    parts.append("")
    if usable_claims:
        parts.append(
            f"{len(usable_claims)} finding"
            f"{'s were' if len(usable_claims) != 1 else ' was'} recovered from the sources above. "
            "Each finding links to the page it came from."
        )
    else:
        parts.append("There is insufficient evidence to draw a conclusion from this run.")

    return "\n".join(parts)


# ── Section surgery ──────────────────────────────────────────────────────────


def ensure_sections(markdown: str, query: str) -> tuple[str, bool]:
    """Guarantee *markdown* has a readable section structure.

    Returns ``(markdown, changed)``. If the text has no headings at all, an
    Executive Summary is prepended so the reader never sees a wall of text.
    The model's own words are preserved verbatim underneath.
    """
    if extract_sections(markdown):
        return markdown, False

    subject = query.strip() or "this query"
    summary = f"## Executive Summary\n\nFindings on {subject}, as reported by the analysis engine.\n"
    return f"{summary}\n{markdown.strip()}\n", True


def dedupe_sources(lines: list[str]) -> list[str]:
    """Drop repeated source lines, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


# ── Public entry point ───────────────────────────────────────────────────────


def shape_report(
    raw: str | None,
    *,
    query: str = "",
    claims: list[Any] | None = None,
    entities: list[Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> ShapedReport:
    """Validate and shape a model report into readable, honest output.

    Args:
        raw: The model's reply, verbatim.
        query: The user's research query.
        claims: Extracted claims, used to synthesize a report when needed.
        entities: Extracted entities, used to synthesize a report when needed.
        sources: Scraped documents (``{"url", "title", ...}``) for the Sources list.

    Returns:
        :class:`ShapedReport`. ``markdown`` is never empty.
    """
    sources = sources or []
    citation_urls = [str(s.get("url", "") or "") for s in sources if str(s.get("url", "") or "").startswith("http")]

    body = (raw or "").strip()

    # ── Case 1: the model refused ────────────────────────────────────────
    # Checked BEFORE the empty-length gate: a refusal such as "I'm sorry, I
    # cannot help with that." is shorter than _MIN_REPORT_CHARS, so testing
    # length first would mislabel it RAW_EMPTY and lose the real reason.
    if looks_like_refusal(body):
        markdown = build_evidence_report(query, claims, entities, sources)
        log.warning("report.shape.model_refusal", query=query)
        return ShapedReport(
            markdown=markdown,
            quality=ReportQuality.NO_EVIDENCE if not (claims or entities or sources) else ReportQuality.REFUSAL,
            sections=extract_sections(markdown),
            citation_count=len(set(citation_urls)),
        )

    # ── Case 2: nothing usable from the model ────────────────────────────
    if len(body) < _MIN_REPORT_CHARS:
        quality = ReportQuality.RAW_EMPTY
        markdown = build_evidence_report(query, claims, entities, sources)
        log.warning("report.shape.empty_model_output", query=query, raw_len=len(body))
        return ShapedReport(
            markdown=markdown,
            quality=ReportQuality.NO_EVIDENCE if not (claims or entities or sources) else quality,
            sections=extract_sections(markdown),
            citation_count=len(set(citation_urls)),
        )

    # ── Case 3: usable prose — enforce readability ───────────────────────
    markdown, added_sections = ensure_sections(body, query)

    # Append a Sources section only when the report cites nothing at all.
    # A structured report that deliberately omits a source list (e.g. a
    # short summary) is left exactly as the model wrote it.
    notes: list[str] = []
    cites_anything = "http://" in markdown or "https://" in markdown
    if citation_urls and not cites_anything:
        unique = dedupe_sources([_source_line(u, "") for u in dict.fromkeys(citation_urls)])
        markdown = markdown.rstrip() + "\n\n## Sources\n\n" + "\n".join(unique) + "\n"
        notes.append("sources_appended")

    quality = ReportQuality.NO_SECTIONS if added_sections else ReportQuality.OK
    return ShapedReport(
        markdown=markdown,
        quality=quality,
        sections=extract_sections(markdown),
        citation_count=len(set(citation_urls)),
        notes=notes,
    )
