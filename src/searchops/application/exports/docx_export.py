"""Export a ReportDocument to Word (.docx).

Same contract as the deck exporter: the reader must be able to check what they
are given. Headings map to real Word heading styles (so the navigation pane and
any downstream tooling work), every source URL is preserved, and per Q4 the
claim provenance is attached to each cited block rather than discarded.

Word is the format that ends up in a compliance record or an email thread, so
fidelity matters more here than anywhere else in the export path.

This module produces a complete, professional report artifact with:
- A cover page with title, date, and trust summary
- An automatic table of contents
- Headers and footers with page numbers
- A glossary of extracted entities
- A study guide derived from the report content
- All formatting syntax properly rendered (no raw ** or __ leaking)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 — used at runtime for out_dir.mkdir()
from typing import Any

import structlog
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

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


def _clean_markdown(text: str) -> str:
    """Sanitize markdown formatting syntax into clean document text."""
    if not text:
        return ""
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^#+\s*", "", text)  # second pass for nested markers
    text = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", text)  # bold+italic
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^>\s*", "", text)
    # LaTeX math → Unicode (for DOCX text rendering)
    from searchops.application.exports.math_render import render_math_for_text

    text = render_math_for_text(text)
    return text.strip()


def export_docx(
    doc: ReportDocument,
    *,
    out_dir: Path,
    title: str = "Research Report",
    study_guide: Any = None,
) -> Path:
    """Write the document as a .docx and return its path.

    ``study_guide`` is a pre-built :class:`StudyGuide` supplied by the caller.
    The exporter makes no LLM call itself; when omitted, the study-guide
    section is skipped rather than fabricated.
    """
    document = Document()

    # Set document metadata
    document.core_properties.title = title
    document.core_properties.subject = doc.query
    document.core_properties.author = "SEARCHOps Research Platform"
    document.core_properties.created = datetime.now(UTC)
    document.core_properties.modified = datetime.now(UTC)

    # Add page numbers to footer
    _add_page_numbers(document)

    # 1. Cover page
    _render_cover(document, doc, title)

    # 2. Table of contents
    _render_toc(document, doc, study_guide)

    # 3. Report body
    _render_body(document, doc)

    # 4. Glossary
    _render_glossary(document, doc)

    # 5. Study guide
    _render_study_guide(document, doc, study_guide)

    # 6. Sources
    _render_sources(document, doc)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(title, ".docx")
    document.save(str(path))
    log.info("export.docx.written", path=str(path))
    return path


def _add_page_numbers(document: Document) -> None:
    """Add page numbers to the footer of every section."""
    for section in document.sections:
        footer = section.footer
        paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run()
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(102, 102, 102)
        # Add PAGE field
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        fldChar1 = OxmlElement("w:fldChar")
        fldChar1.set(qn("w:fldCharType"), "begin")
        instrText = OxmlElement("w:instrText")
        instrText.set(qn("xml:space"), "preserve")
        instrText.text = "PAGE"
        fldChar2 = OxmlElement("w:fldChar")
        fldChar2.set(qn("w:fldCharType"), "end")
        run._r.append(fldChar1)
        run._r.append(instrText)
        run._r.append(fldChar2)


def _render_cover(document: Document, doc: ReportDocument, title: str) -> None:
    """Render a professional cover page."""
    # Add vertical spacing
    for _ in range(6):
        document.add_paragraph()

    # Title
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(title)
    run.font.size = Pt(32)
    run.font.bold = True
    run.font.color.rgb = RGBColor(26, 26, 46)

    # Subtitle (query)
    if doc.query:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(doc.query)
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(102, 102, 102)

    # Quality note
    if doc.quality and doc.quality != "ok":
        msg = doc.user_message or f"Report quality: {doc.quality}"
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(f"⚠ {msg}")
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(204, 102, 0)

    # Trust summary
    trust = trust_summary(doc)
    if trust["total"] > 0:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(f"Trust: {trust_headline(trust)}")
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(68, 68, 68)

    # Date and branding
    today = datetime.now(UTC).strftime("%B %d, %Y")
    for _ in range(4):
        document.add_paragraph()
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"Generated {today}")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(153, 153, 153)

    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("SEARCHOps Research Platform")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(153, 153, 153)

    document.add_page_break()


def _render_toc(document: Document, doc: ReportDocument, study_guide: Any = None) -> None:
    """Render a table of contents from the document's headings."""
    toc_entries: list[tuple[str, int]] = []
    for block in doc.visible_blocks():
        if block.kind is BlockKind.HEADING:
            level = block.level or 1
            text = _clean_markdown(block.text)
            # Sections only: subsections belong in the body, and the title is
            # already on the cover.
            if level > 2 or (level == 1 and text.lower() in (doc.report_title().lower(), "research report")):
                continue
            if text:
                toc_entries.append((text, level))

    if not toc_entries:
        return

    document.add_heading("Table of Contents", level=1)
    for i, (text, level) in enumerate(toc_entries, 1):
        indent = "  " * (level - 1)
        p = document.add_paragraph(f"{indent}{i}. {text}")
        p.paragraph_format.space_after = Pt(2)

    # Add glossary and study guide entries — only for sections that will
    # actually be rendered. Listing a section that is absent is a broken
    # table of contents, and it is exactly what happened once the study-guide
    # renderer stopped producing a section: the TOC still promised it.
    #
    # A section the report already provides as its own heading is not added
    # again, so the report's "Sources" heading is not listed twice.
    already_listed = {text.strip().lower() for text, _ in toc_entries}
    n = len(toc_entries)
    if doc.entities and "glossary" not in already_listed:
        n += 1
        p = document.add_paragraph(f"{n}. Glossary")
        p.paragraph_format.space_after = Pt(2)
    if study_guide is not None and getattr(study_guide, "questions", None) and "study guide" not in already_listed:
        n += 1
        p = document.add_paragraph(f"{n}. Study Guide")
        p.paragraph_format.space_after = Pt(2)

    document.add_page_break()


def _render_body(document: Document, doc: ReportDocument) -> None:
    """Render the report body blocks."""
    # Trust header at the top of the body
    trust = trust_summary(doc)
    has_glance = any(
        b.kind is BlockKind.HEADING and "evidence at a glance" in b.text.lower() for b in doc.visible_blocks()
    )
    if trust["total"] > 0 and not has_glance:
        p = document.add_paragraph()
        run = p.add_run(f"📊 Trust Summary: {trust_headline(trust)}")
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(68, 68, 68)
        run.italic = True

    if doc.quality and doc.quality != "ok":
        msg = doc.user_message or f"Report quality: {doc.quality}"
        p = document.add_paragraph()
        run = p.add_run(f"⚠ {msg}")
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(204, 102, 0)
        run.italic = True

    for block in doc.visible_blocks():
        _render_block(document, block, doc)


def _render_glossary(document: Document, doc: ReportDocument) -> None:
    """Render the glossary of extracted entities."""
    entries = build_glossary(doc)
    if not entries:
        return

    document.add_page_break()
    document.add_heading("Glossary", level=1)
    for entry in entries:
        p = document.add_paragraph()
        run = p.add_run(entry.term)
        run.bold = True
        run.font.size = Pt(11)
        if entry.entity_type:
            run = p.add_run(f" ({entry.entity_type})")
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(102, 102, 102)
        if entry.definition:
            p = document.add_paragraph(entry.definition)
            p.paragraph_format.left_indent = Inches(0.3)
            for run in p.runs:
                run.font.size = Pt(10)


def _render_study_guide(document: Document, doc: ReportDocument, guide: Any = None) -> None:
    """Render the study guide with questions and answers.

    ``guide`` is a pre-built :class:`StudyGuide` supplied by the caller. The
    previous version read ``getattr(doc, '_study_guide', None)`` — an attribute
    nothing in the codebase ever set — so this section silently never appeared
    in any exported Word file. With no guide, the section is skipped rather
    than fabricated.
    """
    if not guide or not getattr(guide, "questions", None):
        return

    document.add_page_break()
    document.add_heading("Study Guide", level=1)
    for i, q in enumerate(guide.questions, 1):
        p = document.add_paragraph()
        run = p.add_run(f"Q{i}. {q.question}")
        run.bold = True
        run.font.size = Pt(11)
        p = document.add_paragraph(f"A. {q.answer}")
        p.paragraph_format.left_indent = Inches(0.3)
        for run in p.runs:
            run.font.size = Pt(10)
        if q.section:
            p = document.add_paragraph(f"Section: {q.section}")
            p.paragraph_format.left_indent = Inches(0.3)
            for run in p.runs:
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(102, 102, 102)

    if guide.note:
        p = document.add_paragraph()
        run = p.add_run(guide.note)
        run.italic = True
        run.font.size = Pt(9)


def _footnote_text(doc: ReportDocument, block: ReportBlock) -> str:
    """Provenance text for a cited block.

    Empty when the block carries no claims — never a fabricated attribution.
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
    return "Claims supporting this section: " + "; ".join(lines)


_INLINE = re.compile(
    r"(\*\*[^*]+\*\*|(?<![*\w])\*[^*\n]+\*(?!\*)|(?<![\w])_[^_\n]+_(?![\w])|`[^`]+`"
    r"|\[[^\]]+\]\([^)\s]+\)|\[\d{1,3}\](?!\())"
)


def _add_inline(paragraph: Any, text: str) -> None:
    """Add *text* to *paragraph* as runs: bold, italic, code, links, superscript citations."""
    from searchops.application.exports.math_render import render_math_for_text

    text = render_math_for_text(text)
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos : m.start()])
        token = m.group(0)
        if token.startswith("**"):
            paragraph.add_run(token[2:-2]).bold = True
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
        elif re.fullmatch(r"\[\d{1,3}\]", token):
            run = paragraph.add_run(token)
            run.font.superscript = True
            run.font.color.rgb = RGBColor(110, 110, 110)
        elif token.startswith("["):
            label, url = re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", token).groups()  # type: ignore[union-attr]
            paragraph.add_run(f"{label} ({url})" if not url.startswith("#") else label)
        else:
            paragraph.add_run(token[1:-1]).italic = True
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _render_table(document: Document, text: str) -> None:
    rows = []
    for line in text.strip().split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{1,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    if not rows:
        return
    width = len(rows[0])
    table = document.add_table(rows=len(rows), cols=width)
    try:
        table.style = document.styles["Light Grid Accent 1"]
    except KeyError:
        table.style = document.styles["Table Grid"]
    for r, cells in enumerate(rows):
        for c in range(width):
            cell = table.cell(r, c)
            para = cell.paragraphs[0]
            _add_inline(para, cells[c] if c < len(cells) else "")
            for run in para.runs:
                run.font.size = Pt(9.5)
                if r == 0:
                    run.bold = True
    document.add_paragraph()


def _render_block(document: Document, block: ReportBlock, doc: ReportDocument) -> None:
    if block.kind is BlockKind.HEADING:
        level = min(block.level or 2, 9)
        document.add_heading(_clean_markdown(block.text), level=level)
        return

    if block.kind is BlockKind.SOURCE:
        return  # rendered once, at the end

    if block.kind is BlockKind.LIST:
        for line in block.text.split("\n"):
            numbered = re.match(r"^\s*\d+[.)]\s+", line)
            item = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", line).strip()
            if item:
                p = document.add_paragraph(style="List Number" if numbered else "List Bullet")
                _add_inline(p, item)
    elif block.kind is BlockKind.TABLE:
        _render_table(document, block.text)
    elif block.kind is BlockKind.QUOTE:
        p = document.add_paragraph()
        _add_inline(p, " ".join(ln.lstrip("> ").strip() for ln in block.text.split("\n")))
        if "Intense Quote" in [s.name for s in document.styles]:
            p.style = document.styles["Intense Quote"]
    elif block.kind is BlockKind.CODE:
        p = document.add_paragraph(block.text.strip("`"))
        for run in p.runs:
            run.font.name = "Consolas"
            run.font.size = Pt(9)
    else:
        p = document.add_paragraph()
        _add_inline(p, block.text.strip())

    # Provenance is shown inline through [n] markers; a block without any
    # carries its supporting claims as a note so the binding is never lost.
    footnote = "" if re.search(r"\[\d{1,3}\](?!\()", block.text) else _footnote_text(doc, block)
    if footnote:
        p = document.add_paragraph()
        run = p.add_run(footnote)
        run.italic = True
        run.font.size = Pt(9)


def _render_sources(document: Document, doc: ReportDocument) -> None:
    urls: list[str] = []
    seen: set[str] = set()
    for block in doc.visible_blocks():
        if block.kind is not BlockKind.SOURCE:
            continue
        for url in re.findall(r"https?://[^\s)\]\"']+", block.text):
            if url not in seen:
                seen.add(url)
                urls.append(url)

    if not urls:
        return

    document.add_page_break()
    document.add_heading("Sources", level=1)
    for url in urls:
        document.add_paragraph(url, style="List Bullet")


def _safe_filename(title: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "report"
    return f"{slug[:60]}{suffix}"
