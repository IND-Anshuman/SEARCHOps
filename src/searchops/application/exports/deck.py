"""Export a ReportDocument to PowerPoint.

A deck is only useful if the reader can still check it. Every source URL from
the report is carried into the deck, and each slide's speaker notes record the
claims that back it (Q4) — that provenance binding is the part a generic
"markdown to slides" converter cannot produce, because it has no claim→block
binding to read from.

Design notes:
- Slides are grouped by H1 sections (not every H3), keeping the deck concise.
  A 55-slide deck is a chore, not a briefing.
- SOURCE blocks are collected into a final Sources slide. They are never
  folded into a content slide, because a citation belongs in one findable
  place.
- Hidden blocks are omitted: the export reflects what the user chose to show.
- An empty document still yields a deck with a single explanatory slide. A
  file that cannot be opened is worse than no file, because the user finds out
  after sending it.
- A trust summary slide and a glossary slide are included when data exists.
"""

from __future__ import annotations

import re
from pathlib import Path  # noqa: TC003 — used at runtime for out_dir.mkdir()
from typing import Any

import structlog
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from searchops.application.exports.glossary import build_glossary
from searchops.application.report_document import (
    BlockKind,
    ReportBlock,
    ReportDocument,
    _field,
    trust_headline,
    trust_summary,
)

log = structlog.get_logger(__name__)

_MAX_BULLETS_PER_SLIDE = 6
_MAX_CHARS_PER_BULLET = 120


def _clean_markdown(text: str) -> str:
    """Sanitize markdown formatting syntax into clean presentation text."""
    if not text:
        return ""
    # Strip markdown headers at start of line (including nested like ## ### Title)
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^#+\s*", "", text)  # second pass for nested markers
    # Strip bold and italics: **text**, __text__, *text*, _text_
    text = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", text)  # bold+italic
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    # Convert markdown links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Strip inline backticks `code` -> code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Strip blockquote markers
    text = re.sub(r"^>\s*", "", text)
    # Clean bullet markers at start of string
    text = re.sub(r"^[-*•\d\.]+\s*", "", text)
    # Citation markers belong in the speaker notes' provenance, not on a slide.
    text = re.sub(r"\s*\[\d{1,3}\](?!\()", "", text)
    # LaTeX math → Unicode (for PPTX text rendering)
    from searchops.application.exports.math_render import render_math_for_text

    text = render_math_for_text(text)
    # Normalize excessive whitespace
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def export_deck(
    doc: ReportDocument,
    *,
    out_dir: Path,
    title: str = "Research Report",
    study_guide: Any = None,
) -> Path:
    """Write the document as a .pptx and return its path.

    ``study_guide`` is a pre-built :class:`StudyGuide` supplied by the caller.
    The exporter makes no LLM call itself; when omitted, the study-guide
    slide is skipped rather than fabricated.

    Raises on a genuine failure — an export that silently returns nothing is
    how a user ends up sending an empty deck.
    """
    prs = Presentation()
    prs.slide_width = Inches(13.333)  # 16:9 widescreen
    prs.slide_height = Inches(7.5)

    # 1. Title slide
    _add_title_slide(prs, doc, title)

    # 2. Trust summary slide (if claims exist)
    trust = trust_summary(doc)
    if trust["total"] > 0:
        _add_trust_slide(prs, doc, trust)

    # 3. Content slides — grouped by H1 sections
    sections = _group_by_h1(doc)
    if not sections:
        _add_plain_slide(prs, title, [_EMPTY_NOTE])
    for heading, blocks in sections:
        _add_content_slide(prs, heading, blocks, doc)

    # 4. Glossary slide (if entities exist)
    _add_glossary_slide(prs, doc)

    # 5. Study guide slide (if supplied by the caller)
    _add_study_guide_slide(prs, doc, study_guide)

    # 6. Sources slide
    _add_sources_slide(prs, doc)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(title, ".pptx")
    prs.save(str(path))
    log.info("export.deck.written", path=str(path), slides=len(prs.slides))
    return path


def _claim_provenance(doc: ReportDocument, block: ReportBlock) -> str:
    """Speaker-note text recording the claims that back this block.

    Returns an empty string when the block carries no claims — never a
    fabricated attribution.
    """
    if not block.claim_ids:
        return ""
    by_id = {str(_field(c, "id", "")): c for c in doc.claims}
    lines: list[str] = []
    for cid in block.claim_ids:
        claim = by_id.get(cid)
        if claim is None:
            continue
        text = str(_field(claim, "text", "") or "").strip()
        if not text:
            continue
        url = ""
        for citation in _field(claim, "citations", None) or []:
            url = str(_field(citation, "url", "") or "")
            if url.startswith("http"):
                break
        lines.append(f"{text}{f'  [{url}]' if url else ''}")
    if not lines:
        return ""
    return "Claims backing this slide:\n" + "\n".join(f"- {ln}" for ln in lines)


# ── Internals ────────────────────────────────────────────────────────────────

_EMPTY_NOTE = "This report has no content. The research run produced no readable output."


def _add_title_slide(prs: Presentation, doc: ReportDocument, title: str) -> None:
    """Add a professional title slide with dark background."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

    # Dark background
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(26, 26, 46)

    # Title
    title_box = slide.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(11.7), Inches(1.5))
    tf = title_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = RGBColor(255, 255, 255)
    p.alignment = PP_ALIGN.LEFT

    # Subtitle (query)
    if doc.query:
        sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(3.5), Inches(11.7), Inches(0.8))
        tf = sub_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = doc.query
        p.font.size = Pt(16)
        p.font.color.rgb = RGBColor(204, 204, 204)
        p.alignment = PP_ALIGN.LEFT

    # Quality note
    if doc.quality and doc.quality != "ok":
        msg = doc.user_message or f"Report quality: {doc.quality}"
        warn_box = slide.shapes.add_textbox(Inches(0.8), Inches(4.3), Inches(11.7), Inches(0.5))
        tf = warn_box.text_frame
        p = tf.paragraphs[0]
        p.text = f"⚠ {msg}"
        p.font.size = Pt(12)
        p.font.color.rgb = RGBColor(255, 153, 153)

    # Trust summary on title slide
    trust = trust_summary(doc)
    if trust["total"] > 0:
        trust_box = slide.shapes.add_textbox(Inches(0.8), Inches(5.0), Inches(11.7), Inches(0.6))
        tf = trust_box.text_frame
        p = tf.paragraphs[0]
        p.text = f"📊 {trust_headline(trust)}"
        p.font.size = Pt(14)
        p.font.color.rgb = RGBColor(74, 158, 255)

    # Branding
    brand_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.5), Inches(11.7), Inches(0.4))
    tf = brand_box.text_frame
    p = tf.paragraphs[0]
    p.text = "SEARCHOps Research Platform"
    p.font.size = Pt(10)
    p.font.color.rgb = RGBColor(153, 153, 153)


def _add_trust_slide(prs: Presentation, doc: ReportDocument, trust: dict) -> None:
    """Add a trust summary slide."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Title
    title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
    tf = title_box.text_frame
    p = tf.paragraphs[0]
    p.text = "📊 Trust Summary"
    p.font.size = Pt(28)
    p.font.bold = True
    p.font.color.rgb = RGBColor(26, 26, 46)

    # Trust content
    content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(12), Inches(5.5))
    tf = content_box.text_frame
    tf.word_wrap = True

    p = tf.paragraphs[0]
    p.text = trust_headline(trust)
    p.font.size = Pt(18)
    p.font.color.rgb = RGBColor(51, 51, 51)
    p.space_after = Pt(12)

    # Add breakdown
    if trust["total"] > 0:
        p = tf.add_paragraph()
        p.text = f"Total findings: {trust['total']}"
        p.font.size = Pt(14)
        p.space_after = Pt(6)

        if trust["supported"]:
            p = tf.add_paragraph()
            p.text = f"  ✓ Well-supported: {trust['supported']}"
            p.font.size = Pt(14)
            p.font.color.rgb = RGBColor(0, 128, 0)
            p.space_after = Pt(6)

        if trust["contested"]:
            p = tf.add_paragraph()
            p.text = f"  ⚠ Contested: {trust['contested']}"
            p.font.size = Pt(14)
            p.font.color.rgb = RGBColor(204, 102, 0)
            p.space_after = Pt(6)

        if trust["refuted"]:
            p = tf.add_paragraph()
            p.text = f"  ✗ Refuted: {trust['refuted']}"
            p.font.size = Pt(14)
            p.font.color.rgb = RGBColor(204, 0, 0)
            p.space_after = Pt(6)

        if trust["unverified"]:
            p = tf.add_paragraph()
            p.text = f"  ? Unverified: {trust['unverified']}"
            p.font.size = Pt(14)
            p.font.color.rgb = RGBColor(102, 102, 102)
            p.space_after = Pt(6)

    if doc.gaps:
        p = tf.add_paragraph()
        p.text = f"\nGaps identified: {len(doc.gaps)}"
        p.font.size = Pt(14)
        p.font.color.rgb = RGBColor(102, 102, 102)


def _add_plain_slide(prs: Presentation, title: str, lines: list[str]) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1].text_frame
    body.text = lines[0] if lines else _EMPTY_NOTE


def _group_by_h1(doc: ReportDocument) -> list[tuple[str, list[ReportBlock]]]:
    """Group visible blocks under their most recent major heading.

    Finds the first heading level after the title (H1) and groups by that
    level. This keeps the deck concise — one slide per major section, not
    per H3. For a report with H2 sections, groups by H2.
    """
    # Find the first heading level after the title — group by that level
    title_level = None
    group_level = None
    for block in doc.visible_blocks():
        if block.kind is BlockKind.HEADING:
            if title_level is None:
                title_level = block.level or 1
            elif group_level is None:
                group_level = block.level
                break

    # If all headings are the same level, group by that level
    if group_level is None:
        group_level = title_level or 1

    sections: list[tuple[str, list[ReportBlock]]] = []
    current: tuple[str, list[ReportBlock]] | None = None

    for block in doc.visible_blocks():
        if block.kind is BlockKind.HEADING:
            level = block.level or 1
            clean_h = _clean_markdown(block.text)
            if level <= group_level:
                # Start a new section (same or higher level heading)
                current = (clean_h or "Section", [])
                sections.append(current)
            elif current is not None:
                # Sub-heading within current section — add as sub-bullet
                current[1].append(block)
        elif block.kind is BlockKind.SOURCE:
            continue  # collected into the Sources slide
        elif current is not None:
            current[1].append(block)

    return [(h, b) for h, b in sections if b]


def _add_content_slide(
    prs: Presentation,
    heading: str,
    blocks: list[ReportBlock],
    doc: ReportDocument,
) -> None:
    bullets: list[str] = []
    sub_bullets: list[str] = []
    for block in blocks:
        if block.kind is BlockKind.HEADING:
            # H2/H3 becomes a sub-bullet header
            text = _clean_markdown(block.text)
            if text:
                sub_bullets.append(f"  ▸ {text}")
        else:
            bullets.extend(_bullets_for(block))
        if len(bullets) + len(sub_bullets) >= _MAX_BULLETS_PER_SLIDE:
            break

    if not bullets and not sub_bullets:
        return

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = heading or "Details"
    body = slide.placeholders[1].text_frame
    body.word_wrap = True

    all_lines = bullets + sub_bullets
    body.text = all_lines[0] if all_lines else ""
    for line in all_lines[1:]:
        p = body.add_paragraph()
        p.text = line
        p.font.size = Pt(14)
        if line.startswith("  ▸"):
            p.font.bold = True
            p.font.size = Pt(13)
            p.font.color.rgb = RGBColor(74, 158, 255)

    for para in body.paragraphs:
        for run in para.runs:
            if not run.font.size:
                run.font.size = Pt(14)

    provenance = "\n\n".join(note for note in (_claim_provenance(doc, b) for b in blocks) if note)
    if provenance and slide.has_notes_slide:
        slide.notes_slide.notes_text_frame.text = provenance


def _bullets_for(block: ReportBlock) -> list[str]:
    text = block.text.strip()
    if not text:
        return []
    if block.kind is BlockKind.LIST:
        raw_lines = text.split("\n")
        cleaned_bullets: list[str] = []
        for ln in raw_lines:
            c = _clean_markdown(ln)
            if c:
                # Truncate long bullets
                if len(c) > _MAX_CHARS_PER_BULLET:
                    c = c[: _MAX_CHARS_PER_BULLET - 3] + "..."
                cleaned_bullets.append(c)
        return cleaned_bullets
    if block.kind is BlockKind.TABLE:
        # One bullet per row: "first cell — header: value; header: value".
        rows = [
            [c.strip() for c in ln.strip().strip("|").split("|")]
            for ln in text.split("\n")
            if ln.strip().startswith("|") and not re.fullmatch(r"[\s|:\-]+", ln)
        ]
        if len(rows) < 2:
            return []
        header, out = rows[0], []
        for row in rows[1:]:
            pairs = "; ".join(
                f"{_clean_markdown(h)}: {_clean_markdown(v)}" for h, v in zip(header[1:], row[1:], strict=False) if v
            )
            line = f"{_clean_markdown(row[0])} — {pairs}" if pairs else _clean_markdown(row[0])
            out.append(line if len(line) <= _MAX_CHARS_PER_BULLET else line[: _MAX_CHARS_PER_BULLET - 3] + "...")
        return out

    cleaned = _clean_markdown(text)
    if not cleaned:
        return []
    # If paragraph is long, split into concise presentation sentences
    if len(cleaned) > 200:
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        valid = [s.strip() for s in sentences if len(s.strip()) > 10]
        if len(valid) > 1:
            result = []
            for s in valid[:_MAX_BULLETS_PER_SLIDE]:
                if len(s) > _MAX_CHARS_PER_BULLET:
                    s = s[: _MAX_CHARS_PER_BULLET - 3] + "..."
                result.append(s)
            # Indicate that content was condensed from a longer paragraph
            if result and "..." not in result[-1]:
                result[-1] = result[-1] + "..."
            return result
    if len(cleaned) > _MAX_CHARS_PER_BULLET:
        cleaned = cleaned[: _MAX_CHARS_PER_BULLET - 3] + "..."
    return [cleaned]


def _add_glossary_slide(prs: Presentation, doc: ReportDocument) -> None:
    """Add a glossary slide with key terms."""
    entries = build_glossary(doc)
    if not entries:
        return

    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Title
    title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
    tf = title_box.text_frame
    p = tf.paragraphs[0]
    p.text = "📖 Glossary"
    p.font.size = Pt(28)
    p.font.bold = True
    p.font.color.rgb = RGBColor(26, 26, 46)

    # Glossary content
    content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(12), Inches(5.5))
    tf = content_box.text_frame
    tf.word_wrap = True

    for i, entry in enumerate(entries[:10]):  # Max 10 terms
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = f"{entry.term}"
        run.font.bold = True
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(26, 26, 46)

        if entry.definition:
            run2 = p.add_run()
            run2.text = f" — {entry.definition[:80]}"
            run2.font.size = Pt(12)
            run2.font.color.rgb = RGBColor(102, 102, 102)

        p.space_after = Pt(8)


def _add_study_guide_slide(prs: Presentation, doc: ReportDocument, guide: Any = None) -> None:
    """Add a study guide slide with questions.

    ``guide`` is a pre-built :class:`StudyGuide` passed down from the caller.
    The previous version read ``getattr(doc, '_study_guide', None)`` — an
    attribute nothing in the codebase ever set — so this slide silently
    never appeared in any deck. With no guide, the slide is skipped rather
    than fabricated.
    """
    if not guide or not getattr(guide, "questions", None):
        return

    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Title
    title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
    tf = title_box.text_frame
    p = tf.paragraphs[0]
    p.text = "📝 Study Guide"
    p.font.size = Pt(28)
    p.font.bold = True
    p.font.color.rgb = RGBColor(26, 26, 46)

    # Questions
    content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(12), Inches(5.5))
    tf = content_box.text_frame
    tf.word_wrap = True

    for i, q in enumerate(guide.questions[:5]):  # Max 5 questions
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = f"Q{i + 1}. {q.question}"
        run.font.bold = True
        run.font.size = Pt(13)
        run.font.color.rgb = RGBColor(26, 26, 46)
        p.space_after = Pt(4)

        p = tf.add_paragraph()
        run = p.add_run()
        run.text = f"   A. {q.answer[:100]}"
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(102, 102, 102)
        p.space_after = Pt(10)


def _add_sources_slide(prs: Presentation, doc: ReportDocument) -> None:
    urls: list[str] = []
    seen: set[str] = set()
    for block in doc.visible_blocks():
        if block.kind is not BlockKind.SOURCE:
            continue
        for url in _urls_in(block.text):
            if url not in seen:
                seen.add(url)
                urls.append(url)

    if not urls:
        return

    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Title
    title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
    tf = title_box.text_frame
    p = tf.paragraphs[0]
    p.text = "📚 Sources"
    p.font.size = Pt(28)
    p.font.bold = True
    p.font.color.rgb = RGBColor(26, 26, 46)

    # Sources list
    content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(12), Inches(5.5))
    tf = content_box.text_frame
    tf.word_wrap = True

    for i, url in enumerate(urls):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"  • {url}"
        p.font.size = Pt(11)
        p.font.color.rgb = RGBColor(74, 158, 255)
        p.space_after = Pt(4)


def _urls_in(text: str) -> list[str]:
    """Extract http(s) URLs, including markdown link targets."""
    import re

    return re.findall(r"https?://[^\s)\]\"']+", text)


def _safe_filename(title: str, suffix: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "report"
    return f"{slug[:60]}{suffix}"
